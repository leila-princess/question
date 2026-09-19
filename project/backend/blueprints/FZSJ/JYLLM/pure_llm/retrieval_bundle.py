"""从工具返回结果汇总为 PDDL/方案生成用的结构化检索包。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class RetrievalError(RuntimeError):
    pass


@dataclass
class RetrievalBundle:
    user_description: str
    neo_methods: List[Dict[str, Any]] = field(default_factory=list)
    neo_projects: List[Dict[str, Any]] = field(default_factory=list)
    neo_equipment: List[Dict[str, Any]] = field(default_factory=list)
    neo_environments: List[Dict[str, Any]] = field(default_factory=list)
    neo_indicators: List[Dict[str, Any]] = field(default_factory=list)
    chroma_chunks: List[Dict[str, Any]] = field(default_factory=list)
    tool_trace: List[Dict[str, Any]] = field(default_factory=list)
    kg_subgraph: Dict[str, Any] = field(default_factory=lambda: {"nodes": [], "edges": []})

    def to_prompt_text(self, max_methods: int = 25) -> str:
        lines = ["## 图谱检索摘要（来自工具，非模拟）"]
        if self.neo_equipment:
            lines.append("### 设备")
            for e in self.neo_equipment[:8]:
                lines.append(f"- {e.get('name')} (Id={e.get('id')})")
        if self.neo_environments:
            lines.append("### 环境")
            for e in self.neo_environments[:8]:
                lines.append(f"- {e.get('name')} (Id={e.get('id')})")
        if self.neo_projects:
            lines.append("### 试验项目")
            for p in self.neo_projects[:12]:
                lines.append(f"- {p.get('name')} (Id={p.get('id')})")
        if self.neo_indicators:
            lines.append("### 指标")
            for i in self.neo_indicators[:12]:
                lines.append(f"- {i.get('name')} (Id={i.get('id')})")
        if self.neo_methods:
            lines.append("### 试验方法（用于 PDDL :action method-<Id>）")
            for m in self.neo_methods[:max_methods]:
                lines.append(
                    f"- method-{m.get('id')} | {m.get('name')} | SYTJ={str(m.get('SYTJ',''))[:60]} | "
                    f"SYNR={str(m.get('SYNR',''))[:80]}"
                )
        if self.chroma_chunks:
            lines.append("### 标准/文档片段（Chroma）")
            for c in self.chroma_chunks[:10]:
                lines.append(f"- {c.get('title','')}: {str(c.get('content',''))[:200]}")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            {
                "neo_methods": self.neo_methods,
                "neo_projects": self.neo_projects,
                "neo_equipment": self.neo_equipment,
                "neo_environments": self.neo_environments,
                "neo_indicators": self.neo_indicators,
                "chroma_chunks": self.chroma_chunks,
                "tool_trace": self.tool_trace,
                "kg_subgraph": self.kg_subgraph,
            },
            ensure_ascii=False,
            indent=2,
        )


def _props(node: Any) -> Dict[str, Any]:
    if isinstance(node, dict):
        if "properties" in node and isinstance(node["properties"], dict):
            return node["properties"]
        return node
    return {}


def _node_label(node: Any) -> str:
    if isinstance(node, dict):
        labs = node.get("labels") or []
        if labs:
            return str(labs[0])
    return ""


def _extract_entity(node: Any, default_label: str = "") -> Optional[Dict[str, Any]]:
    p = _props(node)
    if not p and not isinstance(node, dict):
        return None
    lab = _node_label(node) or default_label
    nid = str(p.get("Id") or p.get("id") or p.get("Name") or "")
    name = str(p.get("Name") or p.get("name") or p.get("Label") or p.get("label") or nid)
    if not name and not nid:
        return None
    ent = {"id": nid or name, "name": name, "label": lab}
    for k in ("SYTJ", "SYGC", "JGPD", "SYNR", "Type", "Class"):
        if p.get(k):
            ent[k] = p.get(k)
    return ent


def _ingest_neo_row(bundle: RetrievalBundle, row: Dict[str, Any]) -> None:
    if row.get("error"):
        return
    n, m = row.get("n"), row.get("m")
    for node, default_lab in ((n, ""), (m, "")):
        ent = _extract_entity(node, default_lab)
        if not ent:
            continue
        lab = ent.get("label") or default_lab
        if lab == "TestMethod":
            if not any(x.get("id") == ent["id"] for x in bundle.neo_methods):
                bundle.neo_methods.append(ent)
        elif lab == "TestProject":
            if not any(x.get("id") == ent["id"] for x in bundle.neo_projects):
                bundle.neo_projects.append(ent)
        elif lab == "Equipment":
            if not any(x.get("id") == ent["id"] for x in bundle.neo_equipment):
                bundle.neo_equipment.append(ent)
        elif lab == "Environment":
            if not any(x.get("id") == ent["id"] for x in bundle.neo_environments):
                bundle.neo_environments.append(ent)
        elif lab == "Indicator":
            if not any(x.get("id") == ent["id"] for x in bundle.neo_indicators):
                bundle.neo_indicators.append(ent)
    # 子图边
    if n and m:
        ne = _extract_entity(n)
        me = _extract_entity(m)
        if ne and me:
            bundle.kg_subgraph["nodes"] = _merge_nodes(bundle.kg_subgraph["nodes"], [ne, me])
            bundle.kg_subgraph["edges"].append([ne["id"], me["id"], str(row.get("r", "CON"))])


def _merge_nodes(existing: List[Dict], new_items: List[Dict]) -> List[Dict]:
    ids = {x.get("id") for x in existing}
    out = list(existing)
    for it in new_items:
        if it.get("id") not in ids:
            out.append(it)
            ids.add(it.get("id"))
    return out


def ingest_tool_result(bundle: RetrievalBundle, tool_name: str, args: Dict[str, Any], result: Dict[str, Any]) -> None:
    neo = list(result.get("neo4jResults") or [])
    chroma = list(result.get("chromaResults") or [])
    bundle.tool_trace.append(
        {
            "tool": tool_name,
            "args": args,
            "neo_count": len(neo),
            "chroma_count": len(chroma),
            "error": result.get("error"),
        }
    )
    for row in neo:
        if isinstance(row, dict):
            _ingest_neo_row(bundle, row)
    for c in chroma:
        if isinstance(c, dict) and not c.get("error"):
            bundle.chroma_chunks.append(
                {
                    "title": c.get("title") or (c.get("metadata") or {}).get("source_file", ""),
                    "content": c.get("content", ""),
                    "metadata": c.get("metadata") or {},
                }
            )


def validate_bundle(bundle: RetrievalBundle) -> None:
    neo_ok = any(
        t.get("neo_count", 0) > 0 and not t.get("error")
        for t in bundle.tool_trace
        if str(t.get("tool", "")).startswith("TOOL_neo")
    )
    chroma_ok = any(
        t.get("chroma_count", 0) > 0 and not t.get("error")
        for t in bundle.tool_trace
        if t.get("tool") == "TOOL_chroma_vector_search"
    )
    if not neo_ok:
        raise RetrievalError("未从 Neo4j 工具获得有效命中，请检查图谱数据或改写试验描述后重试")
    if not chroma_ok:
        raise RetrievalError("未从 Chroma 获得有效命中，请检查向量库或标准文档是否已入库")
