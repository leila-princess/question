#!/usr/bin/env python3
"""
从 Neo4j 图数据库 + Chroma 向量库全量检索数据，生成 500 条问答对。
输出：qa_pairs_500.json / qa_pairs_500.csv / qa_pairs_500.md
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

# 项目 backend 根目录
_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from config import Config  # noqa: E402
from utils.neo4j_flgl_status import flgl_status_cypher_param  # noqa: E402

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
TARGET_COUNT = 500

NODE_LABELS = [
    "EquipmentType",
    "Equipment",
    "Environment",
    "TestProject",
    "Indicator",
    "TestMethod",
    "File",
]

STATUS_PARAM = flgl_status_cypher_param()


def _connect_neo4j():
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        Config.NEO4J_URI,
        auth=(Config.NEO4J_USER, Config.NEO4J_PASSWORD),
    )
    with driver.session() as s:
        s.run("RETURN 1")
    return driver


def _connect_chroma():
    """读取 Chroma 仅需 PersistentClient.get_collection，无需 embedding 模型。"""
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    chroma_path = os.path.abspath(Config.CHROMA_PATH)
    client = chromadb.PersistentClient(
        path=chroma_path,
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    name = Config.CHROMA_COLLECTION
    return client.get_collection(name=name)


def _node_name(props: Dict[str, Any], label: str = "") -> str:
    if label == "EquipmentType":
        for k in ("Type", "Name", "Label"):
            v = str(props.get(k) or "").strip()
            if v:
                return v
    for k in ("Name", "Label", "Type", "Id"):
        v = str(props.get(k) or "").strip()
        if v:
            return v
    return ""


def fetch_neo4j_data(driver) -> Dict[str, Any]:
    """拉取全部节点与关系。"""
    data: Dict[str, Any] = {
        "nodes": defaultdict(list),
        "relations": defaultdict(list),
        "stats": {},
    }
    status_clause = """
    AND (NOT any(l IN labels(n) WHERE l IN ['EquipmentType','Equipment','Environment','TestProject','Indicator','TestMethod','File'])
         OR n.Status IS NULL OR n.Status IN $flgl_enabled_status)
    """

    with driver.session() as session:
        for label in NODE_LABELS:
            cypher = f"""
            MATCH (n:{label})
            WHERE true
            {status_clause.replace('n.', 'n.')}
            RETURN n
            LIMIT 2000
            """
            # EquipmentType 可能没有 Status
            if label == "EquipmentType":
                cypher = f"MATCH (n:{label}) RETURN n LIMIT 500"
            elif label == "File":
                cypher = f"MATCH (n:{label}) RETURN n LIMIT 500"
            rows = session.run(cypher, **STATUS_PARAM)
            for rec in rows:
                n = rec["n"]
                props = dict(n)
                name = _node_name(props, label)
                data["nodes"][label].append(
                    {"id": props.get("Id") or props.get("id"), "name": name, "props": props}
                )

        rel_patterns = [
            ("Equipment", "CON", "TestProject"),
            ("Environment", "CON", "TestProject"),
            ("Equipment", "CON", "Indicator"),
            ("TestProject", "CON", "TestMethod"),
            ("Indicator", "CON", "TestMethod"),
            ("EquipmentType", "HAS", "Equipment"),
            ("TestMethod", "BELONGS_TO", "File"),
            ("TestProject", "HAS", "TestProject"),
        ]
        for fr, rel, to in rel_patterns:
            cypher = f"""
            MATCH (a:{fr})-[r:{rel}]->(b:{to})
            RETURN a.Name AS a_name, a.Type AS a_type, a.Id AS a_id,
                   b.Name AS b_name, b.Type AS b_type, b.Id AS b_id,
                   type(r) AS rel
            LIMIT 3000
            """
            rows = session.run(cypher)
            for rec in rows:
                data["relations"][(fr, rel, to)].append(dict(rec))

    for label in NODE_LABELS:
        data["stats"][label] = len(data["nodes"][label])
    data["stats"]["relations"] = sum(len(v) for v in data["relations"].values())
    return data


def fetch_chroma_data_sqlite(chroma_path: Optional[str] = None) -> Dict[str, Any]:
    """通过 sqlite 直读 Chroma 持久化数据（避免 chromadb 客户端崩溃）。"""
    import sqlite3

    base = chroma_path or os.path.abspath(Config.CHROMA_PATH)
    db_file = os.path.join(base, "chroma.sqlite3")
    if not os.path.isfile(db_file):
        return {"total_chunks": 0, "chunks": [], "by_doc": {}}

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    ids = [r[0] for r in conn.execute("SELECT id FROM embeddings")]
    all_docs: List[Dict[str, Any]] = []
    for eid in ids:
        meta: Dict[str, Any] = {"id": str(eid)}
        doc = ""
        for r in conn.execute(
            "SELECT key, string_value, int_value FROM embedding_metadata WHERE id=?",
            (eid,),
        ):
            k = r["key"]
            if k in ("#document", "chroma:document"):
                doc = r["string_value"] or doc
            elif r["string_value"] is not None:
                meta[k] = r["string_value"]
            elif r["int_value"] is not None:
                meta[k] = r["int_value"]
        meta["content"] = doc
        all_docs.append(meta)
    conn.close()

    by_doc: Dict[str, List[Dict]] = defaultdict(list)
    for d in all_docs:
        key = d.get("doc_id") or d.get("title") or d.get("id") or "unknown"
        by_doc[key].append(d)
    return {"total_chunks": len(all_docs), "chunks": all_docs, "by_doc": dict(by_doc)}


def fetch_chroma_data(collection=None) -> Dict[str, Any]:
    """优先 chromadb API；失败则 sqlite 直读。"""
    if collection is not None:
        try:
            total = collection.count()
            batch = 500
            all_docs: List[Dict[str, Any]] = []
            offset = 0
            while offset < total:
                chunk = collection.get(
                    limit=min(batch, total - offset),
                    offset=offset,
                    include=["documents", "metadatas"],
                )
                docs = chunk.get("documents") or []
                metas = chunk.get("metadatas") or []
                ids = chunk.get("ids") or []
                for i, doc in enumerate(docs):
                    meta = metas[i] if i < len(metas) else {}
                    all_docs.append(
                        {
                            "id": ids[i] if i < len(ids) else "",
                            "content": doc or "",
                            "doc_id": (meta or {}).get("doc_id", ""),
                            "title": (meta or {}).get("title", ""),
                            "type": (meta or {}).get("type", ""),
                            "section": (meta or {}).get("section", ""),
                        }
                    )
                offset += len(docs)
                if not docs:
                    break
            by_doc: Dict[str, List[Dict]] = defaultdict(list)
            for d in all_docs:
                key = d.get("doc_id") or d.get("title") or d.get("id") or "unknown"
                by_doc[key].append(d)
            return {"total_chunks": total, "chunks": all_docs, "by_doc": dict(by_doc)}
        except Exception as e:
            print(f"Chroma API 读取失败，改用 sqlite: {e}")
    return fetch_chroma_data_sqlite()


class QAGenerator:
    def __init__(self, neo: Dict[str, Any], chroma: Dict[str, Any]):
        self.neo = neo
        self.chroma = chroma
        self.pairs: List[Dict[str, str]] = []
        self.seen_q: Set[str] = set()

    def add(self, question: str, answer: str, category: str = "") -> bool:
        q = re.sub(r"\s+", " ", (question or "").strip())
        a = re.sub(r"\s+", " ", (answer or "").strip())
        if not q or not a or len(a) < 2:
            return False
        key = q.lower()
        if key in self.seen_q:
            return False
        self.seen_q.add(key)
        self.pairs.append({"question": q, "answer": a, "category": category})
        return True

    def _equip_names(self) -> List[str]:
        return [n["name"] for n in self.neo["nodes"]["Equipment"] if n["name"]][:80]

    def _env_names(self) -> List[str]:
        return [n["name"] for n in self.neo["nodes"]["Environment"] if n["name"]][:50]

    def _project_names(self) -> List[str]:
        return [n["name"] for n in self.neo["nodes"]["TestProject"] if n["name"]][:100]

    def _indicator_names(self) -> List[str]:
        return [n["name"] for n in self.neo["nodes"]["Indicator"] if n["name"]][:60]

    def _method_names(self) -> List[str]:
        return [n["name"] for n in self.neo["nodes"]["TestMethod"] if n["name"]][:80]

    def _file_names(self) -> List[str]:
        return [n["name"] for n in self.neo["nodes"]["File"] if n["name"]][:40]

    def _et_names(self) -> List[str]:
        return [n["name"] for n in self.neo["nodes"]["EquipmentType"] if n["name"]][:40]

    def _related(self, fr: str, rel: str, to: str, focus: str) -> List[str]:
        key = (fr, rel, to)
        out: List[str] = []
        for r in self.neo["relations"].get(key, []):
            a = str(r.get("a_name") or r.get("a_type") or "").strip()
            b = str(r.get("b_name") or r.get("b_type") or "").strip()
            if fr == "Equipment" and focus and focus in a:
                if b:
                    out.append(b)
            elif to == "Equipment" and focus and focus in b:
                if a:
                    out.append(a)
            elif fr == "Environment" and focus and focus in a:
                if b:
                    out.append(b)
            elif fr == "TestProject" and focus and focus in a:
                if b:
                    out.append(b)
            elif to == "TestProject" and focus and focus in b:
                if a:
                    out.append(a)
            elif fr == "Indicator" and focus and focus in a:
                if b:
                    out.append(b)
            elif to == "Indicator" and focus and focus in b:
                if a:
                    out.append(a)
            elif fr == "TestMethod" and focus and focus in a:
                if b:
                    out.append(b)
            elif to == "TestMethod" and focus and focus in b:
                if a:
                    out.append(a)
            elif fr == "EquipmentType" and focus and focus in (a, str(r.get("a_type") or "")):
                if b:
                    out.append(b)
        return list(dict.fromkeys(out))

    def generate(self) -> List[Dict[str, str]]:
        self._gen_greeting_and_meta()
        self._gen_enum()
        self._gen_equipment_project()
        self._gen_environment_project()
        self._gen_equipment_indicator()
        self._gen_project_method()
        self._gen_indicator_method()
        self._gen_equipment_type()
        self._gen_environment_props()
        self._gen_standard_file()
        self._gen_method_content()
        self._gen_chroma_doc()
        self._gen_composite()
        self._gen_variants()
        return self.pairs[:TARGET_COUNT]

    def _gen_greeting_and_meta(self):
        self.add("你好", "您好！我是试验知识智能问答助手，可帮您查询装备试验项目、考核指标、试验方法、试验环境及标准文档等内容。", "闲聊")
        self.add("你是谁", "我是基于图数据库与向量知识库的智能问答助手，专注于装备试验领域知识检索与解答。", "闲聊")
        self.add("你能做什么", "我可以回答：某装备有哪些试验项目、某项目有哪些试验方法、某装备要考核哪些指标、试验环境分类与要求、标准文件章节内容等问题。", "闲聊")
        stats = self.neo.get("stats", {})
        stat_ans = "；".join(f"{k}={v}" for k, v in stats.items())
        self.add("图数据库里有多少数据", f"当前图库统计：{stat_ans}。", "元信息")
        self.add("向量库有多少文档片段", f"Chroma 向量库共 {self.chroma.get('total_chunks', 0)} 条文档片段，涵盖 {len(self.chroma.get('by_doc', {}))} 份文档。", "元信息")

    def _gen_enum(self):
        for label, zh, templates in [
            ("TestProject", "试验项目", ["有哪些试验项目", "列举全部试验项目", "试验项目清单有哪些"]),
            ("Environment", "试验环境", ["有哪些试验环境", "试验环境类型有哪些", "环境类别清单"]),
            ("Indicator", "考核指标", ["有哪些考核指标", "考核指标清单", "全部指标有哪些"]),
            ("Equipment", "装备", ["有哪些装备", "装备清单有哪些", "系统里登记了哪些装备"]),
            ("EquipmentType", "装备类型", ["有哪些装备类型", "装备类别有哪些", "二级装备类型清单"]),
        ]:
            names = [n["name"] for n in self.neo["nodes"][label] if n["name"]]
            if not names:
                continue
            ans = f"{zh}共 {len(names)} 条，包括：" + "、".join(names[:30])
            if len(names) > 30:
                ans += f"等（共 {len(names)} 条）。"
            for tpl in templates:
                self.add(tpl, ans, f"枚举-{label}")

    def _gen_equipment_project(self):
        templates = [
            "{e}有哪些试验项目",
            "{e}的试验项目有哪些",
            "{e}需要做哪些试验",
            "查询{e}关联的试验项目",
            "{e}涉及哪些测试项目",
        ]
        for e in self._equip_names():
            projs = self._related("Equipment", "CON", "TestProject", e)
            if not projs:
                continue
            ans = f"{e}相关的试验项目有：" + "、".join(projs[:20])
            if len(projs) > 20:
                ans += f"等（共 {len(projs)} 项）。"
            for tpl in templates:
                self.add(tpl.format(e=e), ans, "装备-试验项目")

    def _gen_environment_project(self):
        templates = [
            "{env}环境下有哪些试验项目",
            "{env}环境对应的试验项目",
            "在{env}条件下要做哪些试验",
        ]
        for env in self._env_names():
            projs = self._related("Environment", "CON", "TestProject", env)
            if not projs:
                continue
            ans = f"{env}环境相关的试验项目包括：" + "、".join(projs[:20])
            for tpl in templates:
                self.add(tpl.format(env=env), ans, "环境-试验项目")

    def _gen_equipment_indicator(self):
        templates = [
            "{e}要考核哪些指标",
            "{e}的考核指标有哪些",
            "{e}需要满足哪些性能指标",
        ]
        for e in self._equip_names():
            inds = self._related("Equipment", "CON", "Indicator", e)
            if not inds:
                continue
            ans = f"{e}的考核指标包括：" + "、".join(inds[:20])
            for tpl in templates:
                self.add(tpl.format(e=e), ans, "装备-考核指标")

    def _gen_project_method(self):
        templates = [
            "{p}有哪些试验方法",
            "{p}的试验方法是什么",
            "查询{p}对应的测试方法",
            "{p}采用哪些试验方法",
        ]
        for p in self._project_names():
            methods = self._related("TestProject", "CON", "TestMethod", p)
            if not methods:
                continue
            ans = f"试验项目「{p}」关联的试验方法有：" + "、".join(methods[:15])
            for tpl in templates:
                self.add(tpl.format(p=p), ans, "项目-试验方法")

    def _gen_indicator_method(self):
        templates = [
            "{ind}指标对应的试验方法",
            "{ind}有哪些试验方法",
            "考核{ind}用什么方法",
        ]
        for ind in self._indicator_names():
            methods = self._related("Indicator", "CON", "TestMethod", ind)
            if not methods:
                continue
            ans = f"指标「{ind}」关联的试验方法包括：" + "、".join(methods[:15])
            for tpl in templates:
                self.add(tpl.format(ind=ind), ans, "指标-试验方法")

    def _gen_equipment_type(self):
        for et in self._et_names():
            equips = self._related("EquipmentType", "HAS", "Equipment", et)
            if not equips:
                continue
            ans = f"装备类型「{et}」下的具体装备有：" + "、".join(equips[:20])
            self.add(f"{et}类装备有哪些", ans, "装备类型-装备")
            self.add(f"属于{et}类型的装备有哪些", ans, "装备类型-装备")

    def _gen_environment_props(self):
        for env_node in self.neo["nodes"]["Environment"][:40]:
            name = env_node["name"]
            if not name:
                continue
            props = env_node["props"]
            parts = []
            for k, label in [
                ("Class", "分类"),
                ("Type", "类型"),
                ("Combination", "组合"),
                ("Limit", "约束"),
                ("Request", "要求"),
                ("Description", "描述"),
            ]:
                v = str(props.get(k) or "").strip()
                if v:
                    parts.append(f"{label}：{v}")
            if not parts:
                continue
            ans = f"试验环境「{name}」的属性如下。" + "；".join(parts)
            self.add(f"{name}环境的分类和要求是什么", ans, "环境属性")
            self.add(f"介绍一下{name}试验环境", ans, "环境属性")

    def _gen_standard_file(self):
        for f in self._file_names()[:30]:
            methods = self._related("TestMethod", "BELONGS_TO", "File", f)
            if methods:
                ans = f"标准/文件「{f}」包含的试验方法有：" + "、".join(methods[:15])
                self.add(f"{f}标准包含哪些试验方法", ans, "标准-方法")
            projs = []
            for p in self._project_names()[:50]:
                stds = self._related("TestProject", "CON", "TestMethod", p)
                # 通过方法找文件较复杂，直接用节点属性
            for file_node in self.neo["nodes"]["File"]:
                if file_node["name"] == f:
                    props = file_node["props"]
                    cid = props.get("Chroma_id") or props.get("Chroma_chunks")
                    if cid:
                        self.add(
                            f"{f}标准文件在知识库中的标识是什么",
                            f"文件「{f}」在系统中的 Chroma 标识为 {cid}。",
                            "标准-元数据",
                        )

    def _gen_method_content(self):
        templates_synr = ["{m}的试验方法内容是什么", "{m}的具体步骤", "{m}方法正文"]
        templates_sytj = ["{m}的试验条件是什么", "{m}试验条件"]
        templates_sygc = ["{m}的试验过程", "{m}怎么做试验"]
        templates_jgpd = ["{m}的结果评定标准", "{m}如何判定结果"]
        for node in self.neo["nodes"]["TestMethod"][:60]:
            name = node["name"]
            if not name:
                continue
            props = node["props"]
            synr = str(props.get("SYNR") or "").strip()
            sytj = str(props.get("SYTJ") or "").strip()
            sygc = str(props.get("SYGC") or "").strip()
            jgpd = str(props.get("JGPD") or "").strip()
            if synr:
                ans = synr[:800] + ("…" if len(synr) > 800 else "")
                for tpl in templates_synr:
                    self.add(tpl.format(m=name), ans, "方法-SYNR")
            if sytj:
                ans = sytj[:600] + ("…" if len(sytj) > 600 else "")
                for tpl in templates_sytj:
                    self.add(tpl.format(m=name), ans, "方法-SYTJ")
            if sygc:
                ans = sygc[:600] + ("…" if len(sygc) > 600 else "")
                for tpl in templates_sygc:
                    self.add(tpl.format(m=name), ans, "方法-SYGC")
            if jgpd:
                ans = jgpd[:600] + ("…" if len(jgpd) > 600 else "")
                for tpl in templates_jgpd:
                    self.add(tpl.format(m=name), ans, "方法-JGPD")

    def _gen_chroma_doc(self):
        by_doc = self.chroma.get("by_doc") or {}
        section_re = re.compile(r"(\d+(?:\.\d+)*)")
        for doc_id, chunks in list(by_doc.items())[:80]:
            if not chunks:
                continue
            title = chunks[0].get("title") or doc_id
            # 文档概述
            preview = (chunks[0].get("content") or "")[:500].strip()
            if preview:
                self.add(
                    f"{title}标准的主要内容是什么",
                    preview + ("…" if len(chunks[0].get("content") or "") > 500 else ""),
                    "向量库-文档概述",
                )
            # 章节问答
            seen_sec: Set[str] = set()
            for ch in chunks[:30]:
                content = (ch.get("content") or "").strip()
                if len(content) < 30:
                    continue
                sec = ch.get("section") or ""
                m = section_re.search(sec or content[:80])
                sec_label = m.group(1) if m else ""
                if sec_label and sec_label not in seen_sec:
                    seen_sec.add(sec_label)
                    q = f"{title}第{sec_label}节的内容是什么"
                    ans = content[:700] + ("…" if len(content) > 700 else "")
                    self.add(q, ans, "向量库-章节")
                # 关键词型
                if "试验" in content[:200]:
                    kw_q = f"{title}中关于试验的规定"
                    if kw_q not in self.seen_q:
                        self.add(kw_q, content[:600], "向量库-关键词")

    def _gen_composite(self):
        for e in self._equip_names()[:25]:
            projs = self._related("Equipment", "CON", "TestProject", e)
            if not projs:
                continue
            methods_all: List[str] = []
            for p in projs[:5]:
                methods_all.extend(self._related("TestProject", "CON", "TestMethod", p))
            methods_all = list(dict.fromkeys(methods_all))
            if methods_all:
                ans = (
                    f"{e}的试验项目包括：" + "、".join(projs[:10])
                    + "；对应试验方法包括：" + "、".join(methods_all[:10])
                )
                self.add(f"{e}有哪些试验项目和方法", ans, "拼装-项目+方法")

        for e in self._equip_names()[:15]:
            inds = self._related("Equipment", "CON", "Indicator", e)
            if not inds:
                continue
            methods_all: List[str] = []
            for ind in inds[:5]:
                methods_all.extend(self._related("Indicator", "CON", "TestMethod", ind))
            methods_all = list(dict.fromkeys(methods_all))
            if methods_all:
                ans = f"{e}的考核指标：" + "、".join(inds[:8]) + "；相关方法：" + "、".join(methods_all[:8])
                self.add(f"{e}的考核指标和试验方法", ans, "拼装-指标+方法")

        for e in self._equip_names()[:10]:
            for p in self._project_names()[:20]:
                if p not in self._related("Equipment", "CON", "TestProject", e):
                    continue
                envs = self._related("Environment", "CON", "TestProject", p)
                # reverse: project -> environment via relation
                env_names = []
                for r in self.neo["relations"].get(("Environment", "CON", "TestProject"), []):
                    if str(r.get("b_name") or "") == p:
                        env_names.append(str(r.get("a_name") or ""))
                env_names = [x for x in env_names if x]
                if env_names:
                    env_node = next(
                        (n for n in self.neo["nodes"]["Environment"] if n["name"] in env_names),
                        None,
                    )
                    if env_node:
                        props = env_node["props"]
                        parts = [f"{k}={props.get(k)}" for k in ("Class", "Type", "Limit", "Request") if props.get(k)]
                        if parts:
                            ans = f"{e}做「{p}」试验时的环境设置：" + "；".join(parts)
                            self.add(f"{e}做{p}试验时的环境条件是什么", ans, "拼装-环境设置")

    def _gen_variants(self):
        """用同义问法扩展至 500 条。"""
        extra_templates = [
            ("{e}相关试验项目", "装备-试验项目", lambda: [(e, self._related("Equipment", "CON", "TestProject", e)) for e in self._equip_names()]),
            ("{p}测试方法列表", "项目-试验方法", lambda: [(p, self._related("TestProject", "CON", "TestMethod", p)) for p in self._project_names()]),
        ]
        synonym_map = {
            "哪些": ["什么", "啥", "有哪些", "包括哪些"],
            "试验": ["测试", "实验"],
            "方法": ["测试方法", "试验方法"],
        }
        base = list(self.pairs)
        for item in base:
            if len(self.pairs) >= TARGET_COUNT:
                break
            q, a, cat = item["question"], item["answer"], item["category"]
            for old, news in synonym_map.items():
                if old in q:
                    for nw in news:
                        nq = q.replace(old, nw, 1)
                        self.add(nq, a, cat + "-变体")
                        if len(self.pairs) >= TARGET_COUNT:
                            return

        # 节点属性直查
        for label, field_q in [
            ("Equipment", "Description"),
            ("TestProject", "Description"),
            ("Indicator", "Description"),
        ]:
            for node in self.neo["nodes"][label][:30]:
                name = node["name"]
                desc = str(node["props"].get(field_q) or "").strip()
                if name and desc and len(self.pairs) < TARGET_COUNT:
                    self.add(f"{name}的说明是什么", desc[:500], f"属性-{label}")

        # 反向关系
        for p in self._project_names()[:30]:
            equips = []
            for r in self.neo["relations"].get(("Equipment", "CON", "TestProject"), []):
                if str(r.get("b_name") or "") == p:
                    equips.append(str(r.get("a_name") or ""))
            equips = [x for x in equips if x]
            if equips:
                ans = f"试验项目「{p}」涉及的装备有：" + "、".join(equips[:15])
                self.add(f"{p}涉及哪些装备", ans, "反向-项目-装备")

        for ind in self._indicator_names()[:25]:
            equips = []
            for r in self.neo["relations"].get(("Equipment", "CON", "Indicator"), []):
                if str(r.get("b_name") or "") == ind:
                    equips.append(str(r.get("a_name") or ""))
            equips = [x for x in equips if x]
            if equips:
                ans = f"考核指标「{ind}」关联的装备有：" + "、".join(equips[:15])
                self.add(f"{ind}指标涉及哪些装备", ans, "反向-指标-装备")


def save_outputs(pairs: List[Dict[str, str]], neo_stats: Dict, chroma_stats: Dict):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(OUT_DIR, "qa_pairs_500.json")
    csv_path = os.path.join(OUT_DIR, "qa_pairs_500.csv")
    md_path = os.path.join(OUT_DIR, "qa_pairs_500.md")
    snapshot_path = os.path.join(OUT_DIR, f"data_snapshot_{ts}.json")

    payload = {
        "generated_at": datetime.now().isoformat(),
        "count": len(pairs),
        "neo4j_stats": neo_stats,
        "chroma_stats": chroma_stats,
        "pairs": pairs,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["question", "answer", "category"])
        w.writeheader()
        w.writerows(pairs)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# ZNWD1 智能问答 500 条问答对\n\n")
        f.write(f"- 生成时间：{payload['generated_at']}\n")
        f.write(f"- 图库统计：{json.dumps(neo_stats, ensure_ascii=False)}\n")
        f.write(f"- 向量库：{chroma_stats.get('total_chunks', 0)} 片段 / {chroma_stats.get('doc_count', 0)} 文档\n\n")
        for i, p in enumerate(pairs, 1):
            f.write(f"## {i}. [{p.get('category', '')}]\n\n")
            f.write(f"**问：** {p['question']}\n\n")
            f.write(f"**答：** {p['answer']}\n\n---\n\n")

    # 精简快照（不含全量 props）
    snap = {
        "neo4j_stats": neo_stats,
        "chroma_total": chroma_stats.get("total_chunks"),
        "chroma_docs": chroma_stats.get("doc_ids", [])[:100],
    }
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)

    print(f"已写入 {len(pairs)} 条问答对:")
    print(f"  JSON: {json_path}")
    print(f"  CSV:  {csv_path}")
    print(f"  MD:   {md_path}")


def main():
    print("连接 Neo4j …", Config.NEO4J_URI)
    driver = _connect_neo4j()
    print("拉取图数据库 …")
    neo = fetch_neo4j_data(driver)
    driver.close()
    print("图库统计:", neo["stats"])

    print("拉取向量库（sqlite）…")
    chroma = fetch_chroma_data(None)
    print(f"Chroma: {chroma['total_chunks']} chunks, {len(chroma['by_doc'])} docs")

    gen = QAGenerator(neo, chroma)
    pairs = gen.generate()
    print(f"生成问答对: {len(pairs)}")

    if len(pairs) < TARGET_COUNT:
        print(f"警告: 仅生成 {len(pairs)} 条（目标 {TARGET_COUNT}），数据量或关系可能不足。")

    save_outputs(
        pairs,
        neo_stats=neo["stats"],
        chroma_stats={
            "total_chunks": chroma["total_chunks"],
            "doc_count": len(chroma.get("by_doc", {})),
            "doc_ids": list((chroma.get("by_doc") or {}).keys()),
        },
    )


if __name__ == "__main__":
    main()
