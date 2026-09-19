"""
确定性方案骨架：用户选择 + Neo4j → 多环境标题树（对齐 JYGZ simplified_template 层级语义）
与 JYLLM 模板编号一致：#### 2.1.{e} 环境 → ##### 2.1.{e}.{p} 项目；方法层原为 ###### 2.1.{e}.{p}.{m}（见 build_method_tree_and_skeleton 内注释块，当前省略方法名标题行，仅保留 METHOD_SENTINEL）。

另提供规则化「规划步骤」Markdown（不调用 LLM）与框架预览（标注数据库/LLM 来源）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Generator, Iterator, List, Optional, Set, Tuple

from config import Config
from utils.neo4j_flgl_status import flgl_status_cypher_param
from blueprints.FZSJ.JYLLM.method_synr_markdown import synr_html_is_substantial
from blueprints.FZSJ.JYLLM.project_tree_utils import rebuild_indicator_forest, rebuild_project_forest

logger = logging.getLogger(__name__)

METHOD_SENTINEL_PREFIX = "<!--JYLLM_METHOD:"
METHOD_SENTINEL_SUFFIX = "-->"

MIN_GRAPH_FIELD_LEN = 20

# 项目在图库中未挂 Environment 节点时仍归入一组；对用户展示为「其他环境」（非「未关联」）
OTHER_ENV_GROUP_ID = "__none__"
OTHER_ENV_DISPLAY_NAME = "其他环境"

_SYNTH_PROJ_PREFIX = "jyllm_synth_proj_"
_SYNTH_IND_PREFIX = "jyllm_synth_ind_"


def _synthetic_method_for_context(synth_id: str, title: str) -> Dict[str, Any]:
    """图库无 CON 方法时占位：生成阶段走 LLM（need_llm 全为真），依据设备/环境/项目或指标补写。"""
    return {
        "id": synth_id,
        "name": title,
        "sytj": "",
        "sygc": "",
        "jgpd": "",
        "synr": "",
        "imageurls": None,
        "files": [],
        "need_llm": {"sytj": True, "sygc": True, "jgpd": True},
        "synthetic_from_context": True,
    }


def _run_cypher(cypher: str, params: Optional[dict] = None) -> List[dict]:
    if not Config.NEO4J_DRIVER:
        return []
    try:
        with Config.NEO4J_DRIVER.session() as session:
            result = session.run(cypher, params or {})
            rows: List[dict] = []
            for r in result:
                d: dict = {}
                for k in r.keys():
                    val = r[k]
                    if hasattr(val, "items") and hasattr(val, "id"):
                        d[k] = dict(val)
                    else:
                        d[k] = val
                rows.append(d)
            return rows
    except Exception as e:
        logger.warning(f"[skeleton_builder] Neo4j: {e}")
        return []


def _node_props(node: Any) -> Dict[str, Any]:
    if not node or not isinstance(node, dict):
        return {}
    out: Dict[str, Any] = {}
    for k, v in node.items():
        if str(k).startswith("_") or k in ("labels",):
            continue
        out[str(k).lower()] = v
    return out


def _norm_id(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _method_need_llm(sytj: str, sygc: str, jgpd: str, synr: str = "") -> Dict[str, bool]:
    """若 SYNR（HTML 试验内容）已充实，则整段走规则填充（SYNR→Markdown），不再拆 LLM 补 sytj/sygc/jgpd。"""
    if synr_html_is_substantial((synr or "").strip()):
        return {"sytj": False, "sygc": False, "jgpd": False}

    def short(s: str) -> bool:
        t = (s or "").strip()
        return len(t) < MIN_GRAPH_FIELD_LEN

    return {"sytj": short(sytj), "sygc": short(sygc), "jgpd": short(jgpd)}


def _need_llm_any(need: Dict[str, bool]) -> bool:
    return bool(need.get("sytj") or need.get("sygc") or need.get("jgpd"))


def _method_source_tag_cn(m: Dict[str, Any]) -> str:
    """用户可见骨架：方法下仅一行来源说明（无英文缩写）。"""
    need = m.get("need_llm") or _method_need_llm(
        str(m.get("sytj") or ""),
        str(m.get("sygc") or ""),
        str(m.get("jgpd") or ""),
        str(m.get("synr") or ""),
    )
    return "（LLM生成）" if _need_llm_any(need) else "（数据库检索）"


def _fetch_methods_graph(method_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    mids = [_norm_id(x) for x in method_ids if _norm_id(x)]
    if not mids:
        return {}
    st = flgl_status_cypher_param()
    cypher = """
    UNWIND $mids AS mid
    MATCH (m:TestMethod)
    WHERE (toString(m.Id) = toString(mid) OR toString(m.id) = toString(mid))
      AND (m).Status IN $flgl_enabled_status
    OPTIONAL MATCH (f:File)-[:BELONGS_TO]->(m)
    WHERE (f) IS NULL OR (f).Status IN $flgl_enabled_status
    RETURN m, collect(DISTINCT f) AS files
    """
    records = _run_cypher(cypher, {**st, "mids": mids})
    out: Dict[str, Dict[str, Any]] = {}
    for r in records:
        m = _node_props(r.get("m") or {})
        mid = _norm_id(m.get("id"))
        if not mid:
            continue
        files_raw = r.get("files") or []
        files: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for f in files_raw:
            if not f:
                continue
            fp = _node_props(f if isinstance(f, dict) else {})
            fid = _norm_id(fp.get("id"))
            if fid and fid in seen:
                continue
            if fid:
                seen.add(fid)
            files.append(
                {
                    "id": fid or _norm_id(fp.get("name")),
                    "name": str(fp.get("name") or fp.get("label") or fid or "未命名文件"),
                }
            )
        out[mid] = {
            "id": mid,
            "name": str(m.get("name") or m.get("label") or mid),
            "source_name": str(m.get("source_name") or m.get("source_id") or "").strip(),
            "sytj": str(m.get("sytj") or m.get("SYTJ") or "").strip(),
            "sygc": str(m.get("sygc") or m.get("SYGC") or "").strip(),
            "jgpd": str(m.get("jgpd") or m.get("JGPD") or "").strip(),
            "synr": str(m.get("synr") or m.get("SYNR") or "").strip(),
            "imageurls": m.get("imageurls") if m.get("imageurls") is not None else m.get("image_urls"),
            "files": files,
        }
    return out


def _user_env_id(display_name: str) -> str:
    """配置表/前端传入的环境名 → 稳定分组 id（与图库 id 区分）。"""
    key = (display_name or "").strip()
    return f"user_env:{key}" if key else OTHER_ENV_GROUP_ID


def _env_from_project_payload(project: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """配置弹窗矩阵给出的项目→环境（优先于图库查询）。"""
    en = str(
        project.get("environmentName")
        or project.get("environment_name")
        or project.get("env_name")
        or ""
    ).strip()
    if not en or en in (OTHER_ENV_DISPLAY_NAME, "其他环境"):
        return None
    return _user_env_id(en), en


def _resolve_project_environment(
    pid: str,
    project: Dict[str, Any],
    user_env_names: List[str],
) -> Tuple[str, str]:
    """
    项目环境：配置表映射 > Neo4j > 仅一个用户环境名时的兜底 > 其他环境。
    「其他环境」不是用户勾选项，仅表示图库与配置均无法解析时的占位桶。
    """
    from_payload = _env_from_project_payload(project)
    if from_payload:
        return from_payload

    eid, ename = _fetch_project_environment(pid)
    if eid != OTHER_ENV_GROUP_ID:
        return eid, ename

    if len(user_env_names) == 1:
        only = (user_env_names[0] or "").strip()
        if only and only not in (OTHER_ENV_DISPLAY_NAME, "其他环境"):
            logger.info(
                "[skeleton_builder] 项目 %s 图库无环境，使用配置唯一环境名: %s",
                pid,
                only,
            )
            return _user_env_id(only), only

    if user_env_names:
        logger.warning(
            "[skeleton_builder] 项目 %s 归入「其他环境」：图库未关联环境，且无法从配置矩阵解析"
            "（已选环境: %s）。请检查 Neo4j 连接/数据或同步本机图库。",
            pid,
            "、".join(user_env_names),
        )
    else:
        logger.warning(
            "[skeleton_builder] 项目 %s 归入「其他环境」：图库无环境且配置未传环境名。",
            pid,
        )
    return OTHER_ENV_GROUP_ID, OTHER_ENV_DISPLAY_NAME


def _env_tuple_from_node(env: Any) -> Optional[Tuple[str, str]]:
    if not env:
        return None
    props = _node_props(env) if not isinstance(env, dict) or "name" not in env else env
    name = str(
        props.get("name")
        or props.get("Name")
        or props.get("label")
        or props.get("Label")
        or ""
    ).strip()
    if not name:
        return None
    eid = _norm_id(props.get("id") or props.get("Id"))
    return (eid or _user_env_id(name)), name


def _fetch_project_environment(pid: str) -> Tuple[str, str]:
    """
    项目所在环境 (env_id, env_name)。
    与 FLGL 图模型一致：Environment -[:CON]- TestProject；无 CON 时用项目/父级 Label 对齐 Environment.Name。
    图库均无法解析时返回 ('__none__', '其他环境')。
    """
    st = flgl_status_cypher_param()
    cypher = """
    MATCH (p:TestProject)
    WHERE (p.Id = $pid OR p.id = $pid OR toString(p.Id) = toString($pid))
      AND (p).Status IN $flgl_enabled_status
    OPTIONAL MATCH (parent:TestProject)-[:HAS]->(p)
    OPTIONAL MATCH (e_in:Environment)-[:CON]->(p)
    WHERE (e_in).Status IN $flgl_enabled_status
    WITH p, parent, collect(DISTINCT e_in) AS envs_in
    OPTIONAL MATCH (p)-[:CON]->(e_out:Environment)
    WHERE (e_out).Status IN $flgl_enabled_status
    WITH p, parent, envs_in + collect(DISTINCT e_out) AS envs_con
    WITH p, parent,
         [e IN envs_con WHERE e IS NOT NULL] AS envs_linked,
         coalesce(p.Label, p.label, '') AS proj_label,
         coalesce(parent.Label, parent.label, '') AS parent_label
    OPTIONAL MATCH (e_lbl:Environment)
    WHERE (e_lbl).Status IN $flgl_enabled_status
      AND (
        e_lbl.Name = proj_label OR e_lbl.Label = proj_label
        OR e_lbl.Name = parent_label OR e_lbl.Label = parent_label
      )
    RETURN p AS proj, parent AS parent,
           envs_linked AS envs_linked,
           collect(DISTINCT e_lbl) AS envs_by_label
    LIMIT 1
    """
    recs = _run_cypher(cypher, {**st, "pid": _norm_id(pid)})
    if not recs:
        return OTHER_ENV_GROUP_ID, OTHER_ENV_DISPLAY_NAME

    row = recs[0]
    for env in row.get("envs_linked") or []:
        picked = _env_tuple_from_node(env)
        if picked:
            return picked

    for env in row.get("envs_by_label") or []:
        picked = _env_tuple_from_node(env)
        if picked:
            return picked

    proj = _node_props(row.get("proj") or {})
    parent = _node_props(row.get("parent") or {})
    label_hint = str(
        proj.get("label")
        or proj.get("Label")
        or parent.get("label")
        or parent.get("Label")
        or ""
    ).strip()
    eid2 = _norm_id(
        proj.get("environmentid") or proj.get("environment_id") or proj.get("EnvironmentId")
    )
    if eid2:
        c3 = """
        MATCH (env:Environment)
        WHERE (env.Id = $eid OR env.id = $eid OR toString(env.Id) = toString($eid))
          AND (env).Status IN $flgl_enabled_status
        RETURN env LIMIT 1
        """
        r3 = _run_cypher(c3, {**st, "eid": eid2})
        if r3:
            picked = _env_tuple_from_node(r3[0].get("env"))
            if picked:
                return picked

    if label_hint and label_hint not in (OTHER_ENV_DISPLAY_NAME, "其他环境"):
        c2 = """
        MATCH (env:Environment)
        WHERE (env).Status IN $flgl_enabled_status
          AND (env.Name = $lbl OR env.Label = $lbl
               OR env.Name CONTAINS $lbl OR $lbl CONTAINS env.Name)
        RETURN env
        ORDER BY size(env.Name) ASC
        LIMIT 1
        """
        r2 = _run_cypher(c2, {**st, "lbl": label_hint})
        if r2:
            picked = _env_tuple_from_node(r2[0].get("env"))
            if picked:
                return picked
        return _user_env_id(label_hint), label_hint

    return OTHER_ENV_GROUP_ID, OTHER_ENV_DISPLAY_NAME


def _label(item: Any, fallback: str = "") -> str:
    if isinstance(item, dict):
        return str(
            item.get("name")
            or item.get("Name")
            or item.get("label")
            or item.get("title")
            or item.get("id")
            or fallback
        ).strip() or fallback
    if item is not None:
        s = str(item).strip()
        return s or fallback
    return fallback


def _user_env_name_list(environments: Optional[List[Any]]) -> List[str]:
    out: List[str] = []
    for e in environments or []:
        if isinstance(e, dict):
            n = str(e.get("name") or e.get("Name") or e.get("env_name") or e.get("label") or "").strip()
            if n:
                out.append(n)
        elif isinstance(e, str) and e.strip():
            out.append(e.strip())
    return out


# 第 2 章 2.1 环境小节书写顺序（与前端 constants/environments、JYGZ 模板语义对齐）
_ENV_SECTION_ORDER_KEYS = (
    "复杂电磁",
    "复杂地理",
    "复杂气象",
    "复杂水文",
    "复杂水声",
    "高原综合",
    "海上综合",
)


def _env_sort_bucket(eid: str, display_name: str) -> int:
    """返回排序桶：0..6 标准类，7 图库中未归入标准名的环境，8 其他环境（最后）。"""
    d = (display_name or "").strip()
    if eid == OTHER_ENV_GROUP_ID or d == OTHER_ENV_DISPLAY_NAME or d == "其他环境":
        return 8
    for i, key in enumerate(_ENV_SECTION_ORDER_KEYS):
        variants = (key, f"{key}环境", f"{key}条件")
        for v in variants:
            if len(v) >= 2 and (v in d or d == v):
                return i
    return 7


def _order_env_ids(env_ids: List[str], env_id_to_name: Dict[str, str], user_env_names: List[str]) -> List[str]:
    """
    环境小节顺序：复杂电磁 → … → 海上综合 →（其余图库环境名）→ 其他环境。
    同桶内：优先按用户在配置里勾选的环境名称顺序 (user_env_names)，再按首次出现顺序。
    """
    user_lower = [(un or "").strip().lower() for un in (user_env_names or []) if (un or "").strip()]
    first_pos = {eid: i for i, eid in enumerate(env_ids)}

    def user_pref_rank(eid: str) -> Tuple[int, int]:
        en = (env_id_to_name.get(eid) or "").strip().lower()
        for j, u in enumerate(user_lower):
            if not u:
                continue
            if u in en or en in u:
                return (0, j)
        return (1, 9999)

    return sorted(
        env_ids,
        key=lambda eid: (
            _env_sort_bucket(eid, env_id_to_name.get(eid, "")),
            user_pref_rank(eid),
            first_pos.get(eid, 999),
        ),
    )


def _methods_for_project_ordered(
    pid: str,
    method_rows: List[Dict[str, Any]],
    graph_by_id: Dict[str, Dict[str, Any]],
    selected_method_ids: Set[str],
) -> List[Dict[str, Any]]:
    st = flgl_status_cypher_param()
    cypher = """
    MATCH (p:TestProject)-[:CON]->(m:TestMethod)
    WHERE (p.Id = $pid OR p.id = $pid OR toString(p.Id) = toString($pid))
      AND (p).Status IN $flgl_enabled_status AND (m).Status IN $flgl_enabled_status
    RETURN m
    """
    by_id: Dict[str, Dict[str, Any]] = {}
    for r in _run_cypher(cypher, {**st, "pid": _norm_id(pid)}):
        mp = _node_props(r.get("m") or {})
        mid = _norm_id(mp.get("id"))
        if mid not in selected_method_ids or mid not in graph_by_id:
            continue
        merged = {**graph_by_id[mid]}
        merged["need_llm"] = _method_need_llm(merged["sytj"], merged["sygc"], merged["jgpd"], merged.get("synr", ""))
        by_id[mid] = merged
    ordered: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for m in method_rows:
        mid = _norm_id(m.get("id"))
        if mid in seen or mid not in by_id:
            continue
        seen.add(mid)
        row = {**by_id[mid], "name": str(m.get("name") or by_id[mid].get("name") or mid)}
        row["need_llm"] = _method_need_llm(row["sytj"], row["sygc"], row["jgpd"], row.get("synr", ""))
        ordered.append(row)
    return ordered


def _methods_for_indicator_ordered(
    iid: str,
    method_rows: List[Dict[str, Any]],
    graph_by_id: Dict[str, Dict[str, Any]],
    selected_method_ids: Set[str],
) -> List[Dict[str, Any]]:
    st = flgl_status_cypher_param()
    cypher = """
    MATCH (i:Indicator)-[:CON]->(m:TestMethod)
    WHERE (i.Id = $iid OR i.id = $iid OR toString(i.Id) = toString($iid))
      AND (i).Status IN $flgl_enabled_status AND (m).Status IN $flgl_enabled_status
    RETURN m
    """
    by_id: Dict[str, Dict[str, Any]] = {}
    for r in _run_cypher(cypher, {**st, "iid": _norm_id(iid)}):
        mp = _node_props(r.get("m") or {})
        mid = _norm_id(mp.get("id"))
        if mid not in selected_method_ids or mid not in graph_by_id:
            continue
        merged = {**graph_by_id[mid]}
        merged["need_llm"] = _method_need_llm(merged["sytj"], merged["sygc"], merged["jgpd"], merged.get("synr", ""))
        by_id[mid] = merged
    ordered: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for m in method_rows:
        mid = _norm_id(m.get("id"))
        if mid in seen or mid not in by_id:
            continue
        seen.add(mid)
        row = {**by_id[mid], "name": str(m.get("name") or by_id[mid].get("name") or mid)}
        row["need_llm"] = _method_need_llm(row["sytj"], row["sygc"], row["jgpd"], row.get("synr", ""))
        ordered.append(row)
    return ordered


def _flatten_methods_from_blocks(environment_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    def _walk_projects(project_blocks: List[Dict[str, Any]]) -> None:
        for pb in project_blocks or []:
            ctx = pb.get("project") or {}
            for m in pb.get("methods") or []:
                mm = dict(m)
                mm["_section"] = "2.1"
                mm["_project_name"] = str(ctx.get("name") or "")
                mm["_indicator_name"] = ""
                out.append(mm)
            _walk_projects(pb.get("children") or [])

    for eb in environment_blocks:
        _walk_projects(eb.get("projects") or [])
    return out


def _project_id(p: Dict[str, Any]) -> str:
    return _norm_id((p or {}).get("projectId") or (p or {}).get("id"))


def _indicator_id(i: Dict[str, Any]) -> str:
    return _norm_id((i or {}).get("indicatorId") or (i or {}).get("id"))


def _dedupe_nodes_preserve_order(nodes: List[Dict[str, Any]], id_getter) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        nid = id_getter(n)
        if not nid or nid in seen:
            continue
        seen.add(nid)
        out.append(n)
    return out


def build_method_tree_and_skeleton(
    *,
    equipment: Optional[List[Any]],
    environments: Optional[List[Any]],
    projects: Optional[List[Any]],
    indicators: Optional[List[Any]],
    methods: Optional[List[Any]],
) -> Dict[str, Any]:
    proj_roots = rebuild_project_forest(projects)
    ind_roots = rebuild_indicator_forest(indicators)

    def _collect_project_nodes(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen: Set[str] = set()

        def _walk(arr: List[Dict[str, Any]]) -> None:
            for node in arr or []:
                if not isinstance(node, dict):
                    continue
                pid = _project_id(node)
                if not pid:
                    continue
                if pid not in seen:
                    seen.add(pid)
                    out.append(node)
                _walk([c for c in (node.get("children") or []) if isinstance(c, dict)])

        _walk(nodes)
        return out

    def _collect_indicator_nodes(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen: Set[str] = set()

        def _walk(arr: List[Dict[str, Any]]) -> None:
            for node in arr or []:
                if not isinstance(node, dict):
                    continue
                iid = _indicator_id(node)
                if not iid:
                    continue
                if iid not in seen:
                    seen.add(iid)
                    out.append(node)
                _walk([c for c in (node.get("children") or []) if isinstance(c, dict)])

        _walk(nodes)
        return out

    proj_list = _collect_project_nodes(proj_roots)
    ind_list = _collect_indicator_nodes(ind_roots)
    method_rows = [m for m in (methods or []) if isinstance(m, dict) and _norm_id(m.get("id"))]

    selected_method_ids = {_norm_id(m.get("id")) for m in method_rows}
    graph_by_id = _fetch_methods_graph(list(selected_method_ids))

    user_env_names = _user_env_name_list(environments)

    # 项目 → 环境分组（保持用户在 proj_list 中首次出现环境的顺序）
    proj_env_pairs: List[Tuple[Dict[str, Any], str, str]] = []
    env_id_to_name: Dict[str, str] = {}
    env_ids_order: List[str] = []
    env_by_project_id: Dict[str, Tuple[str, str]] = {}
    for p in proj_list:
        pid = _project_id(p)
        eid, ename = _resolve_project_environment(pid, p, user_env_names)
        env_by_project_id[pid] = (eid, ename)
        env_id_to_name[eid] = ename
        if eid not in env_ids_order:
            env_ids_order.append(eid)
        proj_env_pairs.append((p, eid, ename))

    env_ids_ordered = _order_env_ids(env_ids_order, env_id_to_name, user_env_names)

    use_pregrouped_env = any(
        isinstance(e, dict) and "projects" in e for e in (environments or [])
    )

    environment_blocks: List[Dict[str, Any]] = []
    lines_int: List[str] = ["## 2. 试验内容及方法", "### 2.1 试验项目", ""]
    lines_disp: List[str] = ["## 2. 试验内容及方法", "### 2.1 试验项目", ""]

    def _emit_project_nodes(
        nodes: List[Dict[str, Any]],
        parent_no: str,
        level: int,
        *,
        eid_for_filter: str = "",
        filter_env: bool = False,
    ) -> List[Dict[str, Any]]:
        emitted: List[Dict[str, Any]] = []
        seq = 0
        for p in nodes or []:
            pid = _project_id(p)
            if not pid:
                continue
            if filter_env and eid_for_filter:
                peid, _pename = env_by_project_id.get(pid, (OTHER_ENV_GROUP_ID, OTHER_ENV_DISPLAY_NAME))
                if peid != eid_for_filter:
                    continue
            seq += 1
            pno = f"{parent_no}.{seq}"
            pname = _label(p, pid or "试验项目")
            mets = _methods_for_project_ordered(pid, method_rows, graph_by_id, selected_method_ids)
            if not mets:
                sid = f"{_SYNTH_PROJ_PREFIX}{pid}"
                mets = [
                    _synthetic_method_for_context(
                        sid,
                        "试验方法（图库无关联，生成时由系统据设备、环境与该项目补写）",
                    )
                ]

            heading_marks = "#" * min(level, 6)
            lines_int.append(f"{heading_marks} {pno} {pname}")
            lines_int.append("")
            lines_disp.append(f"{heading_marks} {pno} {pname}")
            lines_disp.append("")

            row_methods: List[Dict[str, Any]] = []
            for met in mets:
                mid = _norm_id(met.get("id"))
                lines_int.append(f"{METHOD_SENTINEL_PREFIX}{mid}{METHOD_SENTINEL_SUFFIX}")
                lines_int.append("")
                lines_disp.append(_method_source_tag_cn(met))
                lines_disp.append("")
                row_methods.append({**met, "_env_name": ename, "_parent_prefix": pno})

            child_blocks = _emit_project_nodes(
                [c for c in (p.get("children") or []) if isinstance(c, dict)],
                pno,
                level + 1,
                eid_for_filter=eid_for_filter,
                filter_env=False,
            )
            emitted.append({
                "project": {"id": pid, "name": pname, "number": pno},
                "methods": row_methods,
                "children": child_blocks,
            })
        return emitted

    if use_pregrouped_env:
        env_entries = [e for e in (environments or []) if isinstance(e, dict)]
        for ei, env in enumerate(env_entries, 1):
            ename = str(
                env.get("name") or env.get("Name") or env.get("env_name") or env.get("label") or ""
            ).strip() or "环境"
            eid = _user_env_id(ename)
            env_id_to_name[eid] = ename
            lines_int.append(f"#### 2.1.{ei} {ename}")
            lines_int.append("")
            lines_disp.append(f"#### 2.1.{ei} {ename}")
            lines_disp.append("")
            proj_nodes_for_env = rebuild_project_forest(env.get("projects") or [])
            block_projects = _emit_project_nodes(
                proj_nodes_for_env,
                f"2.1.{ei}",
                5,
                filter_env=False,
            )
            environment_blocks.append(
                {"environment": {"id": eid, "name": ename}, "projects": block_projects}
            )
    else:
        for ei, eid in enumerate(env_ids_ordered, 1):
            ename = env_id_to_name.get(eid) or "环境"
            lines_int.append(f"#### 2.1.{ei} {ename}")
            lines_int.append("")
            lines_disp.append(f"#### 2.1.{ei} {ename}")
            lines_disp.append("")

            block_projects = _emit_project_nodes(
                proj_roots,
                f"2.1.{ei}",
                5,
                eid_for_filter=eid,
                filter_env=True,
            )
            environment_blocks.append(
                {"environment": {"id": eid, "name": ename}, "projects": block_projects}
            )

    indicator_methods: List[Dict[str, Any]] = []
    if ind_list:
        lines_int.extend(["", "### 2.2 边界性能指标", ""])
        lines_disp.extend(["", "### 2.2 边界性能指标", ""])
        def _emit_indicator_nodes(nodes: List[Dict[str, Any]], parent_no: str, level: int) -> List[Dict[str, Any]]:
            emitted: List[Dict[str, Any]] = []
            seq = 0
            for ind in nodes or []:
                iid = _indicator_id(ind)
                if not iid:
                    continue
                seq += 1
                ino = f"{parent_no}.{seq}"
                iname = _label(ind, iid or "考核指标")
                heading_marks = "#" * min(level, 6)
                lines_int.append(f"{heading_marks} {ino} {iname}")
                lines_int.append("")
                lines_disp.append(f"{heading_marks} {ino} {iname}")
                lines_disp.append("")
                mets = _methods_for_indicator_ordered(iid, method_rows, graph_by_id, selected_method_ids)
                if not mets:
                    sid = f"{_SYNTH_IND_PREFIX}{iid}"
                    mets = [
                        _synthetic_method_for_context(
                            sid,
                            "试验方法（图库无关联，生成时由系统据设备、环境与该指标补写）",
                        )
                    ]
                block = {"indicator": {"id": iid, "name": iname, "number": ino}, "methods": [], "children": []}
                for met in mets:
                    mid = _norm_id(met.get("id"))
                    lines_int.append(f"{METHOD_SENTINEL_PREFIX}{mid}{METHOD_SENTINEL_SUFFIX}")
                    lines_int.append("")
                    lines_disp.append(_method_source_tag_cn(met))
                    lines_disp.append("")
                    block["methods"].append({**met, "_parent_prefix": ino})
                block["children"] = _emit_indicator_nodes(
                    [c for c in (ind.get("children") or []) if isinstance(c, dict)],
                    ino,
                    level + 1,
                )
                emitted.append(block)
            return emitted

        indicator_methods = _emit_indicator_nodes(ind_roots, "2.2", 4)

    skeleton = "\n".join(lines_int).strip() + "\n"
    skeleton_display = "\n".join(lines_disp).strip() + "\n"

    flat_methods = _flatten_methods_from_blocks(environment_blocks)
    def _flatten_indicator_methods(blocks: List[Dict[str, Any]]) -> None:
        for block in blocks or []:
            for m in block.get("methods") or []:
                mm = dict(m)
                mm["_section"] = "2.2"
                mm["_project_name"] = ""
                mm["_indicator_name"] = str((block.get("indicator") or {}).get("name") or "")
                flat_methods.append(mm)
            _flatten_indicator_methods(block.get("children") or [])

    _flatten_indicator_methods(indicator_methods)

    # 兼容旧字段：首环境 + 扁平 project_methods（供统计）
    first_env = (environment_blocks[0]["environment"] if environment_blocks else {"id": "default_env", "name": "常温常湿"})
    project_methods_flat: List[Dict[str, Any]] = []
    for eb in environment_blocks:
        project_methods_flat.extend(eb.get("projects") or [])

    method_tree = {
        "environment": first_env,
        "environment_blocks": environment_blocks,
        "project_methods": project_methods_flat,
        "indicator_methods": indicator_methods,
    }

    eq_nodes = []
    for e in equipment or []:
        if isinstance(e, dict):
            eq_nodes.append(
                {
                    "id": _norm_id(e.get("id") or str(e.get("key", "")).replace("eq_", "")),
                    "name": _label(e, "设备"),
                }
            )

    plan_steps = _build_plan_steps_markdown(proj_list, ind_list, flat_methods, graph_by_id)
    framework_outline = _build_framework_outline_markdown(environment_blocks, indicator_methods)

    chapters_compat = [
        {
            "index": 1,
            "chapter_index": 1,
            "chapter_title": "1. 试验依据",
            "title": "1. 试验依据",
            "writer_prompt": "",
            "reviewer_prompt": "",
        },
        {
            "index": 2,
            "chapter_index": 2,
            "chapter_title": "2. 试验内容及方法",
            "title": "2. 试验内容及方法",
            "writer_prompt": "",
            "reviewer_prompt": "",
        },
    ]

    return {
        "environment": first_env,
        "environments_ordered": [{"id": eid, "name": env_id_to_name.get(eid, "")} for eid in env_ids_ordered],
        "equipment": eq_nodes,
        "projects": [{"id": _project_id(p), "name": _label(p)} for p in proj_list],
        "indicators": [{"id": _indicator_id(i), "name": _label(i)} for i in ind_list],
        "method_tree": method_tree,
        "skeleton_markdown": skeleton,
        "skeleton_markdown_display": skeleton_display,
        "plan_steps": plan_steps,
        "framework_outline_markdown": framework_outline,
        "chapters": chapters_compat,
    }


def _build_plan_steps_markdown(
    proj_list: List[Dict[str, Any]],
    ind_list: List[Dict[str, Any]],
    flat_methods: List[Dict[str, Any]],
    _graph_by_id: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """三步规则化规划（Markdown），仅名称、无内部编号展示。"""
    # 步骤 1
    lines1 = ["### 规划步骤 1：查找项目、指标对应的试验方法", ""]
    lines1.append(f"- **已选试验项目**（{len(proj_list)} 个）：")
    for p in proj_list:
        lines1.append(f"  - {_label(p, '试验项目')}")
    lines1.append(f"- **已选考核指标**（{len(ind_list)} 个）：")
    if not ind_list:
        lines1.append("  - （无）")
    else:
        for i in ind_list:
            lines1.append(f"  - {_label(i, '考核指标')}")
    lines1.append(
        f"- **纳入第2章「试验内容及方法」的方法小节**共 **{len(flat_methods)}** 个："
        "含图库中与项目/指标存在关联且您已勾选的方法；若某项目或指标在图库中**无关联方法**，仍保留一节方法正文，并由 **LLM** 在生成阶段结合设备、所选环境与该项目/指标名称补写（一）（二）（三）。"
    )
    lines1.append("")

    # 步骤 2：不用英文缩写表头
    lines2 = [
        "### 规划步骤 2：检查试验条件、试验过程与结果评定在图库中的完整程度",
        "",
        f"判定规则：每一小节在图库中的文字达到 **{MIN_GRAPH_FIELD_LEN}** 个字符以上，视为「图库已有可用正文」，否则生成阶段将由系统补写该小节。",
        "",
        "| 方法名称 | 试验条件 | 试验过程 | 结果评定 |",
        "|----------|----------|----------|----------|",
    ]
    for m in flat_methods:
        nm = str(m.get("name") or "方法")
        need = m.get("need_llm") or _method_need_llm(m.get("sytj", ""), m.get("sygc", ""), m.get("jgpd", ""), str(m.get("synr", "")))

        def cell(need_llm: bool) -> str:
            return "待补全" if need_llm else "已满足"

        lines2.append(
            f"| {nm} | {cell(need['sytj'])} | {cell(need['sygc'])} | {cell(need['jgpd'])} |"
        )
    lines2.append("")

    # 步骤 3
    need_m = [m for m in flat_methods if _need_llm_any(m.get("need_llm") or _method_need_llm(m.get("sytj", ""), m.get("sygc", ""), m.get("jgpd", ""), str(m.get("synr", ""))))]
    lines3 = ["### 规划步骤 3：确定需要由系统补写的方法范围", ""]
    if not need_m:
        lines3.append("（无）所选方法均可在图库中直接落文，生成时以图库内容为主。")
    else:
        lines3.append("以下方法至少有一节图库内容过短或为空，生成时将结合检索资料由系统补写：")
        for m in need_m:
            lines3.append(f"- **{m.get('name')}**")
    lines3.append("")

    return [
        {"index": 1, "title": "查找关联方法", "content_markdown": "\n".join(lines1).strip()},
        {"index": 2, "title": "检查试验条件、过程与结果评定", "content_markdown": "\n".join(lines2).strip()},
        {"index": 3, "title": "确定系统补写范围", "content_markdown": "\n".join(lines3).strip()},
    ]


def _build_framework_outline_markdown(
    environment_blocks: List[Dict[str, Any]],
    indicator_methods: List[Dict[str, Any]],
) -> str:
    """方案结构预览：至试验项目/指标；方法名小节标题已省略（见 build_method_tree_and_skeleton 内注释块，可恢复）。"""
    lines: List[str] = ["## 方案结构预览（至项目与指标；方法名标题已省略）", ""]
    for eb in environment_blocks:
        env = eb.get("environment") or {}
        lines.append(f"#### {_label(env, '试验环境')}")
        lines.append("")

        def _walk_projects(project_blocks: List[Dict[str, Any]]) -> None:
            for pb in project_blocks or []:
                pr = pb.get("project") or {}
                pno = str(pr.get("number") or "").strip()
                ptitle = f"{pno} {_label(pr, '试验项目')}".strip()
                lines.append(f"##### {ptitle}")
                lines.append("")
                for m in pb.get("methods") or []:
                    # 恢复方法名 ######：取消下面一行注释（需同步恢复 mi 编号，见 build_method_tree_and_skeleton）。
                    # lines.append(f"###### {m.get('name')}")
                    lines.append(_method_source_tag_cn(m))
                    lines.append("")
                _walk_projects(pb.get("children") or [])

        _walk_projects(eb.get("projects") or [])

    if indicator_methods:
        lines.append("### 2.2 边界性能指标")
        lines.append("")

        def _walk_indicators(blocks: List[Dict[str, Any]]) -> None:
            for block in blocks or []:
                ind = block.get("indicator") or {}
                ino = str(ind.get("number") or "").strip()
                ititle = f"{ino} {_label(ind, '考核指标')}".strip()
                lines.append(f"#### {ititle}")
                lines.append("")
                for m in block.get("methods") or []:
                    # 恢复方法名 #####：取消下面一行注释（需同步 skeleton 内 2.2 方法标题块）。
                    # lines.append(f"##### {m.get('name')}")
                    lines.append(_method_source_tag_cn(m))
                    lines.append("")
                _walk_indicators(block.get("children") or [])

        _walk_indicators(indicator_methods)
    return "\n".join(lines).strip() + "\n"


def build_locked_ch2_heading_segments(
    environments: Optional[List[Any]],
    projects: Optional[List[Any]],
    indicators: Optional[List[Any]],
    include_section_2_2: bool,
) -> Tuple[str, str]:
    """
    生成 2.1 / 2.2 锁定标题树 Markdown（与 build_method_tree_and_skeleton 编号一致，不含方法占位符）。
    供 Planner / Writer 插入 PLAN_SEGMENT，与 JYGZ simplified_template 层级对齐。
    """
    mt = build_method_tree_and_skeleton(
        equipment=None,
        environments=environments,
        projects=projects,
        indicators=indicators if include_section_2_2 else [],
        methods=[],
    )
    skel = str(mt.get("skeleton") or "")
    heading_lines: List[str] = []
    for line in skel.splitlines():
        stripped = line.strip()
        if stripped.startswith(METHOD_SENTINEL_PREFIX) or stripped == METHOD_SENTINEL_SUFFIX:
            continue
        if METHOD_SENTINEL_PREFIX in stripped and METHOD_SENTINEL_SUFFIX in stripped:
            continue
        heading_lines.append(line)
    # 去掉因剥离占位符产生的连续空行（保留标题间单空行）
    compact: List[str] = []
    for line in heading_lines:
        if not line.strip() and compact and not compact[-1].strip():
            continue
        compact.append(line)
    full = "\n".join(compact).strip()
    marker_new = "### 2.2 边界性能指标"
    marker_old = "### 2.2 边界性能考核"
    idx = full.find(marker_new)
    marker = marker_new
    if idx < 0:
        idx = full.find(marker_old)
        marker = marker_old if idx >= 0 else marker_new
    if not include_section_2_2 or idx < 0:
        return full, "无考核指标，本段不适用。\n"
    return full[:idx].strip(), full[idx:].strip()


def _method_db_full_text(m: Dict[str, Any]) -> str:
    """图库方法正文摘要（供大纲预览）。"""
    synr = str(m.get("synr") or m.get("SYNR") or "").strip()
    parts: List[str] = []
    if synr and synr_html_is_substantial(synr):
        try:
            from blueprints.FZSJ.JYLLM.method_synr_markdown import synr_html_to_markdown

            md = synr_html_to_markdown(
                synr,
                str(m.get("id") or "preview"),
                m.get("imageurls"),
                m.get("files"),
            ).strip()
            if md:
                parts.append(md)
        except Exception:
            parts.append(synr[:1500])
    for key, label in [("sytj", "试验条件"), ("sygc", "试验过程"), ("jgpd", "结果评定")]:
        v = str(m.get(key) or "").strip()
        if v:
            parts.append(f"**{label}**\n{v}")
    return "\n\n".join(parts).strip()


def _method_has_db_data(m: Dict[str, Any]) -> bool:
    need = m.get("need_llm") or _method_need_llm(
        str(m.get("sytj") or ""),
        str(m.get("sygc") or ""),
        str(m.get("jgpd") or ""),
        str(m.get("synr") or ""),
    )
    return not _need_llm_any(need)


def _outline_method_node(
    m: Dict[str, Any],
    level: int,
    device_display_name: str,
    environment_conditions: str,
    equipment_names: str,
    env_name: str = "",
    project_name: str = "",
    indicator_name: str = "",
) -> Dict[str, Any]:
    from blueprints.FZSJ.JYLLM.search_tool import match_reference_files

    mid = _norm_id(m.get("id"))
    has_db = _method_has_db_data(m)
    db_full = _method_db_full_text(m) if has_db else ""
    db_preview = (db_full[:200] + "…") if len(db_full) > 200 else db_full
    q = " ".join(
        x
        for x in [
            device_display_name or equipment_names,
            environment_conditions,
            env_name,
            project_name,
            indicator_name,
            str(m.get("name") or ""),
        ]
        if x
    ).strip()
    refs = match_reference_files(q[:500]) if q else []
    return {
        "id": mid,
        "title": str(m.get("name") or mid or "试验方法"),
        "type": "method",
        "level": level,
        "hasDbData": has_db,
        "dbPreview": db_preview,
        "dbFull": db_full,
        "suggestedReferenceFiles": refs,
        "children": [],
    }


def build_outline_tree(
    method_tree: Dict[str, Any],
    device_display_name: str = "",
    environment_conditions: str = "",
    equipment_names: str = "",
    indicators: Optional[List[Any]] = None,
) -> List[Dict[str, Any]]:
    """构建前端目录勾选树：ch1 + ch2(2.1 试验项目 / 2.2 考核指标)，与骨架 Markdown 层级一致。"""
    ch1: Dict[str, Any] = {
        "id": "ch1",
        "title": "1. 试验依据",
        "type": "chapter",
        "level": 1,
        "children": [],
    }
    ch2: Dict[str, Any] = {
        "id": "ch2",
        "title": "2. 试验内容及方法",
        "type": "chapter",
        "level": 1,
        "children": [],
    }

    def _walk_projects(
        project_blocks: List[Dict[str, Any]],
        env_name: str,
        level: int,
    ) -> List[Dict[str, Any]]:
        nodes: List[Dict[str, Any]] = []
        for pb in project_blocks or []:
            proj = pb.get("project") or {}
            pid = _norm_id(proj.get("id"))
            pname = str(proj.get("name") or pid or "试验项目")
            pnode: Dict[str, Any] = {
                "id": f"proj_{pid}",
                "title": pname,
                "type": "project",
                "level": level,
                "children": [],
            }
            for met in pb.get("methods") or []:
                pnode["children"].append(
                    _outline_method_node(
                        met,
                        level + 1,
                        device_display_name,
                        environment_conditions,
                        equipment_names,
                        env_name=env_name,
                        project_name=pname,
                    )
                )
            pnode["children"].extend(
                _walk_projects(pb.get("children") or [], env_name, level + 1)
            )
            nodes.append(pnode)
        return nodes

    def _walk_indicators(blocks: List[Dict[str, Any]], level: int) -> List[Dict[str, Any]]:
        nodes: List[Dict[str, Any]] = []
        for block in blocks or []:
            ind = block.get("indicator") or {}
            iid = _norm_id(ind.get("id"))
            iname = str(ind.get("name") or iid or "考核指标")
            inode: Dict[str, Any] = {
                "id": f"ind_{iid}",
                "title": iname,
                "type": "indicator",
                "level": level,
                "children": [],
            }
            for met in block.get("methods") or []:
                inode["children"].append(
                    _outline_method_node(
                        met,
                        level + 1,
                        device_display_name,
                        environment_conditions,
                        equipment_names,
                        indicator_name=iname,
                    )
                )
            inode["children"].extend(_walk_indicators(block.get("children") or [], level + 1))
            nodes.append(inode)
        return nodes

    env_nodes: List[Dict[str, Any]] = []
    for eb in method_tree.get("environment_blocks") or []:
        env = eb.get("environment") or {}
        eid = _norm_id(env.get("id"))
        ename = str(env.get("name") or "环境")
        env_nodes.append({
            "id": f"env_{eid}",
            "title": ename,
            "type": "environment",
            "level": 3,
            "children": _walk_projects(eb.get("projects") or [], ename, 4),
        })

    if env_nodes:
        ch2["children"].append({
            "id": "section_2_1",
            "title": "2.1 试验项目",
            "type": "chapter",
            "level": 2,
            "children": env_nodes,
        })

    ind_blocks = list(method_tree.get("indicator_methods") or [])
    if not ind_blocks and indicators:
        ind_roots = rebuild_indicator_forest(indicators)
        if ind_roots:
            ind_blocks = [
                {
                    "indicator": {
                        "id": _indicator_id(ind),
                        "name": _label(ind, "考核指标"),
                        "number": f"2.2.{seq}",
                    },
                    "methods": [],
                    "children": [],
                }
                for seq, ind in enumerate(ind_roots, 1)
                if _indicator_id(ind)
            ]

    if ind_blocks:
        ch2["children"].append({
            "id": "section_2_2",
            "title": "2.2 边界性能指标",
            "type": "chapter",
            "level": 2,
            "children": _walk_indicators(ind_blocks, 3),
        })

    return [ch1, ch2]


def iter_plan_stream_events(
    payload: Dict[str, Any],
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    """整包推送规划正文（无 LLM、体积极小），由前端缓冲区 + 打字机展示。"""
    tid = payload.get("task_id", "")
    envs = payload.get("environments_ordered") or []
    env_line = "、".join(
        str(e.get("name") or e.get("id") or "").strip()
        for e in envs
        if isinstance(e, dict) and (e.get("name") or e.get("id"))
    ).strip() or "—"

    parts: List[str] = [f"### 规划分析\n\n**涉及试验环境**：{env_line}\n\n---\n\n"]
    for step in payload.get("plan_steps") or []:
        title = str(step.get("title") or "").strip()
        body = str(step.get("content_markdown") or "").strip()
        parts.append(f"## {title}\n\n{body}\n\n---\n\n")
    parts.append(str(payload.get("framework_outline_markdown") or "").strip())
    thinking_md = "\n".join(parts).strip() + "\n"

    yield "plan_thinking_start", {"task_id": tid}
    yield "plan_thinking_blob", {"markdown": thinking_md}
    yield "plan_thinking_done", {"task_id": tid}

    sk_disp = str(payload.get("skeleton_markdown_display") or "").strip() + "\n"
    yield "plan_outline_start", {"task_id": tid}
    yield "plan_outline_blob", {"markdown": sk_disp}
    yield "plan_outline_done", {"task_id": tid}

    yield "plan_complete", {
        "task_id": tid,
        "success": True,
        "chapters": payload.get("chapters") or [],
        "environments_ordered": payload.get("environments_ordered") or [],
        "equipment": payload.get("equipment") or [],
        "projects": payload.get("projects") or [],
        "indicators": payload.get("indicators") or [],
        "skeleton_markdown_display": payload.get("skeleton_markdown_display") or "",
        "outline_tree": payload.get("outline_tree") or [],
    }
