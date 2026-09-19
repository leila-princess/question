"""
formatters：检索结果 → 可直接下发的 Markdown 答案（database_direct）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .answer_markdown import html_fragment_to_markdown, property_field_label
from .enum_inference import node_type_zh


def _node_props(row: Dict[str, Any], key: str = "n") -> Dict[str, Any]:
    v = row.get(key)
    if isinstance(v, dict):
        props = v.get("properties") if isinstance(v.get("properties"), dict) else v
        return props if isinstance(props, dict) else {}
    return {}


def _node_labels(row: Dict[str, Any], key: str = "n") -> List[str]:
    v = row.get(key)
    if isinstance(v, dict):
        labs = v.get("labels")
        if isinstance(labs, list):
            return [str(x) for x in labs]
    return []


def _display_name(props: Dict[str, Any], node_type: str = "") -> str:
    if not props:
        return ""
    if node_type == "EquipmentType":
        for k in ("Type", "Name", "Label", "name", "Id", "id"):
            v = str(props.get(k) or "").strip()
            if v:
                return v
        return ""
    for k in ("Name", "name", "Label", "Type", "Id", "id"):
        v = str(props.get(k) or "").strip()
        if v:
            return v
    return ""


def _node_label(row: Dict[str, Any], key: str = "n", node_type: str = "") -> str:
    props = _node_props(row, key)
    if not node_type:
        labs = _node_labels(row, key)
        node_type = labs[0] if labs else ""
    return _display_name(props, node_type)


def format_project_to_method(neo_rows: List[Dict[str, Any]], project_name: str) -> str:
    methods: List[str] = []
    seen: set = set()
    for row in neo_rows or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        for side in ("m", "n"):
            labels = _node_labels(row, side)
            if "TestMethod" not in labels:
                continue
            nm = _node_label(row, side)
            if nm and nm not in seen:
                seen.add(nm)
                methods.append(nm)
    if not methods:
        return f"未在清单库中找到测试项目「{project_name}」关联的试验方法，请核对项目名称或换一种问法。"
    lines = [f"测试项目「{project_name}」关联的试验方法如下（共 {len(methods)} 条）：", ""]
    for i, m in enumerate(methods, 1):
        lines.append(f"{i}. {m}")
    return "\n".join(lines)


def _row_matches_project_keyword(row: Dict[str, Any], project_keyword: str) -> bool:
    if not isinstance(row, dict) or row.get("error"):
        return False
    kw = re.sub(r"\s+", "", (project_keyword or "").strip()).lower()
    if not kw:
        return False
    cores = {kw}
    if kw.endswith("测试项目"):
        cores.add(kw[:-4])
    elif kw.endswith("试验项目"):
        cores.add(kw[:-4])
    elif kw.endswith("测试"):
        cores.add(kw[:-2])
    elif kw.endswith("试验"):
        cores.add(kw[:-2])
    for side in ("m", "n"):
        labels = _node_labels(row, side)
        if "TestProject" not in labels:
            continue
        nm = re.sub(r"\s+", "", _node_label(row, side)).lower()
        if not nm:
            continue
        for core in cores:
            if core and (core in nm or nm in core):
                return True
    return False


def format_projects_to_method(neo_rows: List[Dict[str, Any]], project_names: List[str]) -> str:
    sections: List[str] = []
    for name in project_names:
        subset = [r for r in (neo_rows or []) if _row_matches_project_keyword(r, name)]
        sections.append(format_project_to_method(subset, name))
    return "\n\n".join(sections)


def _collect_method_names(neo_rows: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    seen: set = set()
    for row in neo_rows or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        for side in ("m", "n"):
            if "TestMethod" not in _node_labels(row, side):
                continue
            nm = _node_label(row, side, "TestMethod")
            if nm and nm not in seen:
                seen.add(nm)
                names.append(nm)
    return names


def _rows_with_methods(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        for side in ("m", "n"):
            if "TestMethod" in _node_labels(row, side):
                out.append(row)
                break
    return out


def _resolve_composite_proj_method_rows(
    step_results: Optional[List[Dict[str, Any]]],
    neo_rows: List[Dict[str, Any]],
    *,
    has_env: bool,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    s1_rows = _rows_for_step(step_results, "s1") or neo_rows
    s2_rows = _rows_for_step(step_results, "s2") or []
    s3_rows = _rows_for_step(step_results, "s3") or []
    method_rows = _rows_with_methods(s3_rows) or _rows_with_methods(s2_rows) or _rows_with_methods(neo_rows)
    if has_env:
        proj_rows = s2_rows if s2_rows else s1_rows
        return proj_rows, method_rows
    return s1_rows, method_rows


def _format_equip_projects_methods_composite(
    equip: str,
    s1_rows: List[Dict[str, Any]],
    s2_rows: List[Dict[str, Any]],
    *,
    retrieval_question: str = "",
    environment_label: str = "",
) -> str:
    """拼装/多步「项目+方法」：对齐 gold 句式，避免 per-project「未在清单库」触发 no_retrieval。"""
    entries = _collect_testproject_entries(s1_rows)
    proj_names = _unique_project_names(entries)
    methods = _collect_method_names(s2_rows)
    rq = (retrieval_question or "").strip()
    multi_step = bool(re.search(r"请先|第一步|再告诉", rq))
    env_lbl = (environment_label or "").strip()

    if env_lbl and proj_names:
        if methods:
            return (
                f"{equip}在{env_lbl}环境下需做 "
                + "、".join(proj_names)
                + "；对应方法："
                + "、".join(methods)
            )
        return f"{equip}在{env_lbl}环境下的试验项目有：" + "、".join(proj_names) + "。"

    if multi_step:
        if proj_names:
            step1 = f"第一步：{equip}的试验项目包括 " + "、".join(proj_names) + "。"
        else:
            step1 = ""
        if methods:
            step2 = "第二步：对应试验方法包括 " + "、".join(methods) + "。"
        elif proj_names:
            step2 = "第二步：对应试验方法包括（暂未关联到清单方法）。"
        else:
            step2 = ""
        body = "".join(x for x in (step1, step2) if x)
        return body or "未检索到项目或方法。"

    parts: List[str] = []
    if proj_names:
        parts.append(f"{equip}的试验项目包括：" + "、".join(proj_names))
    if methods:
        parts.append("对应试验方法包括：" + "、".join(methods))
    if parts:
        return "；".join(parts)
    return "未检索到项目或方法。"


def format_enum_nodes(neo_rows: List[Dict[str, Any]], node_type: str) -> str:
    if node_type == "TestProject":
        return format_test_projects_hierarchical(neo_rows)
    names: List[str] = []
    seen: set = set()
    for row in neo_rows or []:
        if not isinstance(row, dict):
            continue
        if node_type == "EquipmentType":
            props = _node_props(row, "n")
            nm = _equipment_type_title(props)
        else:
            nm = _node_label(row, "n", node_type)
        if nm and nm not in seen:
            seen.add(nm)
            names.append(nm)
    zh = node_type_zh(node_type)
    if not names:
        return f"未找到可列举的{zh}。"
    if node_type == "EquipmentType":
        head = f"二级装备类型（共 {len(names)} 条，展示前 {min(len(names), 50)} 条）："
    elif node_type == "Equipment":
        listed = "、".join(names[:50])
        return f"装备共 {len(names)} 条，包括：{listed}"
    else:
        head = f"{zh}（共 {len(names)} 条，展示前 {min(len(names), 50)} 条）："
    body = "\n".join(f"- {n}" for n in names[:50])
    return f"{head}\n{body}"


def format_direct_doc_answer(chroma_rows: List[Dict[str, Any]], *, max_len: int = 2000) -> str:
    """向量/文档题：直出 top-1 检索片段原文，禁止 LLM 改写。"""
    rows = [
        c
        for c in (chroma_rows or [])
        if isinstance(c, dict) and not c.get("error") and str(c.get("content") or "").strip()
    ]
    if not rows:
        return "知识库未检索到相关标准片段。"
    content = str(rows[0].get("content") or "").strip()
    if len(content) > max_len:
        content = content[:max_len]
    return content


def format_chroma_brief(chroma_rows: List[Dict[str, Any]], max_items: int = 5) -> str:
    parts: List[str] = []
    for c in (chroma_rows or [])[:max_items]:
        if not isinstance(c, dict) or c.get("error"):
            continue
        title = str(c.get("title") or c.get("doc_id") or "文档").strip()
        preview = str(c.get("content") or c.get("preview") or "")[:400].strip()
        parts.append(f"**{title}**\n{preview}")
    if not parts:
        return "知识库未检索到相关标准片段。"
    return "\n\n---\n\n".join(parts)


def _is_indicator_map_projection(props: Dict[str, Any]) -> bool:
    """neo_equipment_indicators returns Indicator as flat map (no labels)."""
    if not props:
        return False
    return any(k in props for k in ("hierarchy_type", "parent_id", "parent_name"))


def _collect_indicator_names(neo_rows: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    seen: set = set()

    def add(props: Dict[str, Any]) -> None:
        nm = _display_name(props, "Indicator")
        if nm and nm not in seen:
            seen.add(nm)
            names.append(nm)

    for row in neo_rows or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        for side in ("m", "n"):
            labs = _node_labels(row, side)
            if "Indicator" in labs:
                add(_node_props(row, side))
                continue
            props = _node_props(row, side)
            if _is_indicator_map_projection(props):
                add(props)
    return names


def _format_equipment_by_project_rows(neo_rows: List[Dict[str, Any]], *, focus: str = "") -> str:
    """反查多项目：按试验项目分组列出装备。"""
    by_proj: Dict[str, List[str]] = {}
    seen: Dict[str, set] = {}
    for row in neo_rows or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        proj = ""
        equip = ""
        if "TestProject" in _node_labels(row, "m"):
            proj = _node_label(row, "m", "TestProject")
        elif "TestProject" in _node_labels(row, "n"):
            proj = _node_label(row, "n", "TestProject")
        if "Equipment" in _node_labels(row, "n"):
            equip = _node_label(row, "n", "Equipment")
        elif "Equipment" in _node_labels(row, "m"):
            equip = _node_label(row, "m", "Equipment")
        if not proj or not equip:
            continue
        seen.setdefault(proj, set())
        if equip in seen[proj]:
            continue
        seen[proj].add(equip)
        by_proj.setdefault(proj, []).append(equip)
    if len(by_proj) <= 1:
        return ""
    sections: List[str] = []
    for proj, eqs in by_proj.items():
        head = f"试验项目「{proj}」涉及的装备（共 {len(eqs)} 条）："
        body = "\n".join(f"- {e}" for e in eqs[:200])
        sections.append(f"{head}\n{body}")
    return "\n\n".join(sections)


def _format_equipment_by_indicator_rows(neo_rows: List[Dict[str, Any]], *, focus: str = "") -> str:
    """反查多指标：按考核指标分组列出装备。"""
    by_ind: Dict[str, List[str]] = {}
    seen: Dict[str, set] = {}
    for row in neo_rows or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        ind = ""
        equip = ""
        if "Indicator" in _node_labels(row, "m"):
            ind = _node_label(row, "m", "Indicator")
        elif "Indicator" in _node_labels(row, "n"):
            ind = _node_label(row, "n", "Indicator")
        if "Equipment" in _node_labels(row, "n"):
            equip = _node_label(row, "n", "Equipment")
        elif "Equipment" in _node_labels(row, "m"):
            equip = _node_label(row, "m", "Equipment")
        if not ind or not equip:
            continue
        seen.setdefault(ind, set())
        if equip in seen[ind]:
            continue
        seen[ind].add(equip)
        by_ind.setdefault(ind, []).append(equip)
    if len(by_ind) <= 1:
        return ""
    sections: List[str] = []
    for ind, eqs in by_ind.items():
        head = f"考核指标「{ind}」涉及的装备（共 {len(eqs)} 条）："
        body = "\n".join(f"- {e}" for e in eqs[:200])
        sections.append(f"{head}\n{body}")
    return "\n\n".join(sections)


def format_rel_one_hop(
    neo_rows: List[Dict[str, Any]],
    *,
    from_type: str,
    to_type: str,
    focus: str,
    result_type: str = "",
    where_side: str = "from",
    anchor_type: str = "",
    environment_label: str = "",
) -> str:
    from .enum_inference import node_type_zh

    ws = (where_side or "from").lower()
    target_label = (result_type or "").strip() or (from_type if ws == "to" else to_type)

    if ws == "from" and from_type == "EquipmentType" and to_type == "Equipment":
        return format_equipment_under_type(neo_rows, focus=focus)

    if ws == "to" and from_type == "EquipmentType" and to_type == "Equipment":
        return format_equipment_belongs_to_type(neo_rows, focus=focus)

    if target_label == "TestProject":
        src_type = from_type if ws == "from" else (anchor_type or to_type)
        return format_test_projects_hierarchical(
            neo_rows,
            focus=focus,
            from_type=src_type,
            from_zh=node_type_zh(src_type),
            environment_label=environment_label,
        )

    if target_label == "Indicator":
        names = _collect_indicator_names(neo_rows)
    elif ws == "to" and target_label == "Equipment" and from_type == "Equipment" and to_type == "TestProject":
        grouped = _format_equipment_by_project_rows(neo_rows, focus=focus)
        if grouped:
            return grouped
        names = []
        seen_eq: set = set()
        for row in neo_rows or []:
            if not isinstance(row, dict) or row.get("error"):
                continue
            for side in ("m", "n"):
                if "Equipment" in _node_labels(row, side):
                    nm = _node_label(row, side, "Equipment")
                    if nm and nm not in seen_eq:
                        seen_eq.add(nm)
                        names.append(nm)
    elif ws == "to" and target_label == "Equipment" and from_type == "Equipment" and to_type == "Indicator":
        grouped = _format_equipment_by_indicator_rows(neo_rows, focus=focus)
        if grouped:
            return grouped
        names = []
        seen_eq: set = set()
        for row in neo_rows or []:
            if not isinstance(row, dict) or row.get("error"):
                continue
            for side in ("m", "n"):
                if "Equipment" in _node_labels(row, side):
                    nm = _node_label(row, side, "Equipment")
                    if nm and nm not in seen_eq:
                        seen_eq.add(nm)
                        names.append(nm)
    else:
        names = []
        seen: set = set()
        for row in neo_rows or []:
            if not isinstance(row, dict) or row.get("error"):
                continue
            for side in ("m", "n"):
                labs = _node_labels(row, side)
                if target_label in labs:
                    nm = _node_label(row, side, target_label)
                    if nm and nm not in seen:
                        seen.add(nm)
                        names.append(nm)
    fr_zh = node_type_zh(from_type)
    to_zh = node_type_zh(to_type)
    anchor_zh = node_type_zh(anchor_type or (to_type if ws == "to" else from_type))
    result_zh = node_type_zh(target_label)
    if not names:
        if target_label == "Indicator" and focus:
            return f"未找到装备「{focus}」的考核指标。"
        if ws == "to":
            return f"未找到具备{anchor_zh}「{focus}」的{result_zh}。"
        return f"未找到「{focus}」关联的{to_zh}。"
    if target_label == "Indicator" and focus:
        listed = "、".join(names[:200])
        return f"{focus}的考核指标包括：{listed}"
    if ws == "to":
        head = f"具备{anchor_zh}「{focus}」的{result_zh}（共 {len(names)} 条）："
    else:
        head = f"{fr_zh}「{focus}」关联的{to_zh}（共 {len(names)} 条）："
    body = "\n".join(f"- {n}" for n in names[:200])
    return f"{head}\n{body}"


def _equipment_type_title(props: Dict[str, Any]) -> str:
    if not props:
        return "（未命名类型）"
    name = str(props.get("Name") or props.get("Label") or "").strip()
    type_lv = str(props.get("Type") or "").strip()
    if name and type_lv and type_lv not in name:
        return f"{type_lv} · {name}"
    return name or type_lv or str(props.get("Id") or "（未命名类型）")


def format_equipment_under_type(neo_rows: List[Dict[str, Any]], focus: str = "") -> str:
    """EquipmentType→Equipment：按设备类型分组列出具体设备。"""
    from collections import defaultdict

    groups: Dict[str, List[str]] = defaultdict(list)
    seen: Dict[str, set] = defaultdict(set)
    for row in neo_rows or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        labs_n = _node_labels(row, "n")
        labs_m = _node_labels(row, "m")
        if "EquipmentType" not in labs_n or "Equipment" not in labs_m:
            continue
        gk = _equipment_type_title(_node_props(row, "n"))
        en = _display_name(_node_props(row, "m"), "Equipment")
        if not en:
            continue
        if en in seen[gk]:
            continue
        seen[gk].add(en)
        groups[gk].append(en)
    if not groups:
        hint = f"「{focus}」" if focus else "该条件"
        return f"未找到{hint}下的具体设备。"
    total = sum(len(v) for v in groups.values())
    head = f"设备类型{('「' + focus + '」') if focus else ''}下的具体设备（共 {total} 条，按类型分组）："
    lines = [head, ""]
    for gk in sorted(groups.keys()):
        items = sorted(groups[gk])
        lines.append(f"**{gk}**")
        lines.extend(f"- {x}" for x in items)
        lines.append("")
    return "\n".join(lines).strip()


def format_equipment_belongs_to_type(neo_rows: List[Dict[str, Any]], focus: str = "") -> str:
    """Equipment→EquipmentType（HAS 反查）：某装备属于哪类装备类型。"""
    entries: List[Dict[str, str]] = []
    seen: set = set()
    for row in neo_rows or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        et_props: Dict[str, Any] = {}
        eq_name = ""
        for side in ("n", "m"):
            labs = _node_labels(row, side)
            if "EquipmentType" in labs and not et_props:
                et_props = _node_props(row, side)
            if "Equipment" in labs and not eq_name:
                eq_name = _display_name(_node_props(row, side), "Equipment")
        if not et_props:
            continue
        type_lv1 = str(et_props.get("Type") or "").strip() or "（未填一级类别）"
        type_lv2 = str(et_props.get("Name") or et_props.get("Label") or "").strip() or "（未填二级类型）"
        key = (eq_name or focus, type_lv1, type_lv2)
        if key in seen:
            continue
        seen.add(key)
        entries.append({"eq": eq_name or focus, "type_lv1": type_lv1, "type_lv2": type_lv2})
    equip_label = focus or (entries[0]["eq"] if entries else "该装备")
    if not entries:
        return f"未找到装备「{equip_label}」所属的装备类型。"
    lines = [f"装备「{equip_label}」所属装备类型（共 {len(entries)} 条）：", ""]
    for e in entries:
        lines.append(f"- 一级类别「{e['type_lv1']}」，二级装备类型「{e['type_lv2']}」")
    return "\n".join(lines).strip()


def _project_id(props: Dict[str, Any]) -> str:
    return str(props.get("Id") or props.get("id") or "").strip()


def _collect_testproject_entries(neo_rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen: set = set()

    def add(props: Dict[str, Any], parent_props: Dict[str, Any], from_name: str = "") -> None:
        if not props:
            return
        pid = _project_id(props)
        name = _display_name(props, "TestProject")
        if not name:
            return
        key = (pid or name, from_name)
        if key in seen:
            return
        seen.add(key)
        out.append(
            {
                "id": pid or name,
                "name": name,
                "parent_id": _project_id(parent_props),
                "parent_name": _display_name(parent_props, "TestProject"),
                "from_name": from_name,
                "type": str(props.get("Type") or "").strip(),
            }
        )

    for row in neo_rows or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        parent_props = _node_props(row, "parent")
        if row.get("tp"):
            from_name = _display_name(_node_props(row, "e"), "Equipment") or _display_name(
                _node_props(row, "n"), "Environment"
            )
            add(_node_props(row, "tp"), parent_props, from_name)
            continue
        labs_n = _node_labels(row, "n")
        labs_m = _node_labels(row, "m")
        if "TestProject" in labs_n and "TestMethod" in labs_m:
            continue
        if "TestProject" in labs_m:
            from_name = ""
            if "Equipment" in labs_n:
                from_name = _display_name(_node_props(row, "n"), "Equipment")
            elif "Environment" in labs_n:
                from_name = _display_name(_node_props(row, "n"), "Environment")
            m_props = _node_props(row, "m")
            add(m_props, parent_props, from_name)
            if parent_props and _project_id(parent_props):
                add(parent_props, {}, from_name)
        elif "TestProject" in labs_n:
            n_props = _node_props(row, "n")
            add(n_props, parent_props, "")
            if parent_props and _project_id(parent_props):
                add(parent_props, {}, "")

    return out


def _render_project_tree_lines(entries: List[Dict[str, str]], *, indent: str = "  ") -> List[str]:
    if not entries:
        return []
    by_id: Dict[str, Dict[str, str]] = {e["id"]: e for e in entries}
    children: Dict[str, List[Dict[str, str]]] = {}
    roots: List[Dict[str, str]] = []
    seen_root: set = set()
    for e in entries:
        pid = e.get("parent_id") or ""
        if pid and pid in by_id:
            children.setdefault(pid, []).append(e)
        else:
            rid = e["id"]
            if rid not in seen_root:
                seen_root.add(rid)
                roots.append(e)
    lines: List[str] = []

    def walk(node: Dict[str, str], depth: int) -> None:
        prefix = indent * depth
        nm = node.get("name") or ""
        pnm = node.get("parent_name") or ""
        if depth == 0 and pnm and node.get("parent_id") not in by_id:
            lines.append(f"{prefix}- {nm}（上级：{pnm}）")
        else:
            lines.append(f"{prefix}- {nm}")
        for ch in sorted(children.get(node["id"], []), key=lambda x: x.get("name") or ""):
            walk(ch, depth + 1)

    for r in sorted(roots, key=lambda x: x.get("name") or ""):
        walk(r, 0)
    return lines


def _unique_project_names(entries: List[Dict[str, str]]) -> List[str]:
    names: List[str] = []
    seen: set = set()
    for e in entries or []:
        nm = str(e.get("name") or "").strip()
        if nm and nm not in seen:
            seen.add(nm)
            names.append(nm)
    return names


def _equipment_group_keys_for_focus(from_groups: Dict[str, List[Dict[str, str]]], focus: str) -> List[str]:
    """装备问句：合并精确名 + 型号变体（如 通信设备A + 通信设备A型）。"""
    fn = (focus or "").strip()
    if not fn:
        return sorted(from_groups.keys())
    if fn in from_groups:
        keys = [fn]
        for k in from_groups:
            if k != fn and k.startswith(fn):
                keys.append(k)
        return keys
    matched = [k for k in from_groups if k == fn or fn in k or k in fn]
    return matched if matched else sorted(from_groups.keys())


def format_test_projects_hierarchical(
    neo_rows: List[Dict[str, Any]],
    *,
    focus: str = "",
    from_type: str = "",
    from_zh: str = "",
    environment_label: str = "",
) -> str:
    entries = _collect_testproject_entries(neo_rows)
    if not entries:
        hint = f"「{focus}」" if focus else ""
        return f"未找到{hint}关联的测试项目。"

    from_groups: Dict[str, List[Dict[str, str]]] = {}
    plain: List[Dict[str, str]] = []
    for e in entries:
        fn = (e.get("from_name") or "").strip()
        if fn:
            from_groups.setdefault(fn, []).append(e)
        else:
            plain.append(e)

    lines: List[str] = []
    env_lbl = (environment_label or "").strip()
    if from_groups:
        if focus:
            selected_keys = _equipment_group_keys_for_focus(from_groups, focus)
            from_groups = {k: from_groups[k] for k in selected_keys if k in from_groups}
        if env_lbl and focus and from_type == "Equipment" and from_groups:
            merged_env: List[Dict[str, str]] = []
            for fn in from_groups:
                merged_env.extend(from_groups[fn])
            flat_env = _unique_project_names(merged_env)
            if flat_env:
                return f"{focus}在{env_lbl}环境下的试验项目有：" + "、".join(flat_env) + "。"
        total = len(entries)
        fr = from_zh or (node_type_zh(from_type) if from_type else "实体")
        if len(from_groups) > 1:
            if env_lbl:
                head = f"各装备在试验环境「{env_lbl}」下的试验项目（共 {total} 条，含父子层级）："
            else:
                head = f"各装备关联的试验项目（共 {total} 条，含父子层级）："
        elif env_lbl and focus:
            head = f"装备「{focus}」在试验环境「{env_lbl}」下的试验项目（共 {total} 条，含父子层级）："
        else:
            disp_focus = focus if focus and focus not in from_groups else ""
            head = f"{fr}{('「' + disp_focus + '」') if disp_focus else ''}关联的测试项目（共 {total} 条，含父子层级）："
        if focus and from_type == "Equipment":
            merged: List[Dict[str, str]] = []
            for fn in from_groups:
                merged.extend(from_groups[fn])
            flat = _unique_project_names(merged)
            if flat:
                lines.append(f"{focus}相关的试验项目有：" + "、".join(flat))
                lines.append("")
        lines.append(head)
        lines.append("")
        for fn in sorted(from_groups.keys()):
            lines.append(f"**{fn}**")
            lines.extend(_render_project_tree_lines(from_groups[fn]))
            lines.append("")
        return "\n".join(lines).strip()

    total = len(plain)
    lines.append(f"测试项目（共 {total} 条，含父子层级）：")
    lines.append("")
    lines.extend(_render_project_tree_lines(plain))
    return "\n".join(lines).strip()


def format_equip_project_by_method(
    neo_rows: List[Dict[str, Any]],
    *,
    method_keyword: str = "",
) -> str:
    """设备 → 项目 → 方法 三元组，按设备分组展示（支持单条三节点 Cypher 与两步查询 s2 行）。"""
    from collections import defaultdict

    groups: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    seen: set = set()
    method_display = method_keyword or ""
    project_only: List[str] = []

    for row in neo_rows or []:
        if not isinstance(row, dict) or row.get("error"):
            continue

        e_props: Dict[str, Any] = {}
        tp_props: Dict[str, Any] = {}
        tm_props: Dict[str, Any] = {}

        if row.get("e") or row.get("tp") or row.get("tm"):
            e_props = _node_props(row, "e") or {}
            tp_props = _node_props(row, "tp") or {}
            tm_props = _node_props(row, "tm") or {}
        else:
            labs_n = _node_labels(row, "n")
            labs_m = _node_labels(row, "m")
            if "Equipment" in labs_n and "TestProject" in labs_m:
                e_props = _node_props(row, "n")
                tp_props = _node_props(row, "m")
            elif "TestProject" in labs_n and "TestMethod" in labs_m:
                tp_props = _node_props(row, "n")
                tm_props = _node_props(row, "m")
                pn = _display_name(tp_props, "TestProject")
                if pn and pn not in project_only:
                    project_only.append(pn)
                continue
            elif "Equipment" in labs_n:
                e_props = _node_props(row, "n")
            elif "TestProject" in labs_m:
                tp_props = _node_props(row, "m")

        en = _display_name(e_props, "Equipment")
        pn = _display_name(tp_props, "TestProject")
        mn = _display_name(tm_props, "TestMethod")
        if mn and not method_display:
            method_display = mn
        if not en or not pn:
            continue
        key = (en, pn, mn or method_display or "—")
        if key in seen:
            continue
        seen.add(key)
        groups[en].append((pn, mn or method_display or "—"))

    mk = method_display or "指定试验方法"
    if not groups:
        if project_only:
            lines = [f"采用试验方法「{mk}」的测试项目（共 {len(project_only)} 条，图谱中暂未关联到具体设备）：", ""]
            lines.extend(f"- {p}" for p in project_only[:200])
            return "\n".join(lines)
        return f"未找到采用试验方法「{mk}」的设备与测试项目组合。"
    lines = [f"采用试验方法「{mk}」的设备与测试项目（共 {sum(len(v) for v in groups.values())} 组）：", ""]
    for ename in sorted(groups.keys()):
        lines.append(f"**{ename}**")
        for pn, mn in groups[ename]:
            if mn and mn != "—":
                lines.append(f"- {pn}（方法：{mn}）")
            else:
                lines.append(f"- {pn}")
        lines.append("")
    return "\n".join(lines).strip()


def equip_project_method_rows_nonempty(neo_rows: List[Dict[str, Any]]) -> bool:
    for row in neo_rows or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        if row.get("e") and row.get("tp"):
            return True
        labs_n = _node_labels(row, "n")
        labs_m = _node_labels(row, "m")
        if "Equipment" in labs_n and "TestProject" in labs_m:
            return True
        if "TestProject" in labs_n and "TestMethod" in labs_m:
            return True
    return False


def _strip_html(text: str) -> str:
    s = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", s).strip()


def _field_text(props: Dict[str, Any], *keys: str) -> str:
    alias_map = {
        "Description": ("Description", "描述", "Desc", "description"),
        "Request": ("Request", "Requirement", "要求", "request"),
        "Type": ("Type", "类型", "type"),
        "Class": ("Class", "分级", "class", "Level", "LevelClass"),
        "Combination": ("Combination", "组合", "combination"),
        "Limit": ("Limit", "Constraint", "约束", "limit"),
        "Name": ("Name", "名称", "Label", "name"),
    }
    expanded: List[str] = []
    for k in keys:
        expanded.extend(alias_map.get(k, (k,)))
    for k in expanded:
        v = str(props.get(k) or "").strip()
        if v:
            return v
    return ""


def _field_markdown(props: Dict[str, Any], *keys: str) -> str:
    raw = _field_text(props, *keys)
    if not raw:
        return ""
    from .answer_markdown import field_value_for_environment_property

    fk = keys[0] if keys else ""
    return field_value_for_environment_property(str(fk), raw)


def extract_testmethod_nodes(neo_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for row in neo_rows or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        for side in ("n", "m"):
            if "TestMethod" not in _node_labels(row, side):
                continue
            props = _node_props(row, side)
            nid = str(props.get("Id") or props.get("id") or "").strip()
            name = _node_label(row, side, "TestMethod")
            key = nid or name
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            out.append({"name": name or "未命名方法", "properties": props})
    return out


def method_node_has_substantive_content(props: Dict[str, Any]) -> bool:
    synr = _field_text(props, "SYNR", "synr", "method_content")
    if len(_strip_html(synr)) >= 12:
        return True
    for k in ("SYTJ", "SYGC", "JGPD", "Description"):
        if len(str(props.get(k) or "").strip()) >= 4:
            return True
    return False


def method_nodes_have_content(neo_rows: List[Dict[str, Any]]) -> bool:
    return any(method_node_has_substantive_content(m.get("properties") or {}) for m in extract_testmethod_nodes(neo_rows))


def _format_method_synr_block(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if text.lstrip().startswith("<"):
        return text
    import html as html_mod

    body = html_mod.escape(text).replace("\r\n", "\n").replace("\n", "<br>")
    return f"<p>{body}</p>"


def format_method_content(
    neo_rows: List[Dict[str, Any]],
    *,
    project_name: str = "",
    method_name: str = "",
) -> Tuple[str, bool]:
    methods = extract_testmethod_nodes(neo_rows)
    if not methods:
        subject = method_name or project_name or "该方法"
        return f"未在清单库中找到「{subject}」对应的试验方法节点。", False

    blocks: List[str] = []
    any_content = False
    for item in methods[:5]:
        props = item.get("properties") or {}
        name = str(item.get("name") or "未命名方法")
        synr = _field_text(props, "SYNR", "synr", "method_content")
        sytj = _field_text(props, "SYTJ", "sytj")
        sygc = _field_text(props, "SYGC", "sygc")
        jgpd = _field_text(props, "JGPD", "jgpd")

        if len(_strip_html(synr)) >= 12:
            any_content = True
            blocks.append(_format_method_synr_block(synr))
            continue

        parts: List[str] = []
        has_section = False
        if sytj:
            has_section = True
            parts.append(f"<h4>试验条件</h4>{_format_method_synr_block(sytj)}")
        if sygc:
            has_section = True
            parts.append(f"<h4>试验过程</h4>{_format_method_synr_block(sygc)}")
        if jgpd:
            has_section = True
            parts.append(f"<h4>结果评定</h4>{_format_method_synr_block(jgpd)}")
        if has_section:
            any_content = True
            blocks.append("\n".join(parts))
        else:
            desc = _field_text(props, "Description")
            if desc:
                any_content = True
                blocks.append(f"### {name}\n\n{desc}")

    if not any_content:
        subject = method_name or project_name or methods[0].get("name") or "该方法"
        return f"已找到试验方法「{subject}」，但图库中 SYNR / SYTJ / SYGC / JGPD 均为空，需检索标准文档补充。", False

    head = ""
    if method_name:
        head = ""
    elif project_name:
        head = f"测试项目「{project_name}」关联的试验方法内容如下：\n\n"
    body = "\n".join(blocks).strip()
    return (head + body).strip(), True


def format_node_properties(
    neo_rows: List[Dict[str, Any]],
    *,
    node_type: str = "Environment",
    property_fields: Optional[List[str]] = None,
    focus: str = "",
) -> str:
    fields = property_fields or ["Type", "Level", "Combination", "Requirement", "Constraint", "Name"]
    blocks: List[str] = []
    for row in neo_rows or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        props = _node_props(row, "n")
        if not props:
            continue
        name = _display_name(props, node_type) or focus or "（未命名）"
        lines = [f"### {name}", ""]
        has_any = False
        for fk in fields:
            val = _field_markdown(props, fk)
            if val:
                has_any = True
                label = property_field_label(fk)
                if "\n" in val:
                    lines.extend([f"- **{label}**：", "", val])
                else:
                    lines.append(f"- **{label}**：{val}")
        if has_any:
            blocks.append("\n".join(lines))
    if not blocks:
        label = focus or node_type_zh(node_type)
        if node_type == "Environment":
            return f"未找到试验环境「{label}」的属性信息，请核对环境名称。"
        return f"未找到环境「{label}」的属性信息，请核对环境名称。"
    if len(blocks) == 1 and len(fields) == 1:
        fk = fields[0]
        row = next((r for r in (neo_rows or []) if isinstance(r, dict) and not r.get("error")), None)
        if row:
            props = _node_props(row, "n")
            name = _display_name(props, node_type) or focus or "（未命名）"
            val = _field_markdown(props, fk)
            if val:
                label = property_field_label(fk)
                env_label = focus or name
                if node_type == "Environment":
                    return f"试验环境「{env_label}」的{label}：\n\n{val}"
                return f"环境「{env_label}」的{label}：\n\n{val}"
    if node_type == "Environment":
        head = f"试验环境「{focus}」的属性如下。" if focus else "试验环境属性如下："
    else:
        head = f"环境「{focus}」属性如下：" if focus else "节点属性如下："
    return head + "\n\n" + "\n\n".join(blocks)


def _rows_for_step(step_results: Optional[List[Dict[str, Any]]], step_id: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for sr in step_results or []:
        if not isinstance(sr, dict):
            continue
        if str(sr.get("step_id") or "") != step_id:
            continue
        rows = sr.get("rows")
        if isinstance(rows, list):
            out.extend([r for r in rows if isinstance(r, dict)])
    return out


def _rows_for_intent_step(
    step_results: Optional[List[Dict[str, Any]]],
    index: int,
    step: Any,
) -> List[Dict[str, Any]]:
    for sid in (f"s{index}", f"intent{index}", str(getattr(step, "step_id", "") or "")):
        if not sid:
            continue
        rows = _rows_for_step(step_results, sid)
        if rows:
            return rows
    return []


def format_intent_plan_answer(
    *,
    neo_rows: List[Dict[str, Any]],
    params: Optional[Dict[str, Any]] = None,
    step_results: Optional[List[Dict[str, Any]]] = None,
) -> str:
    from .intent_plan import CHAIN_EQUIP_PROJECT_METHOD, GOAL_REGISTRY, intent_plan_from_dict

    p = params or {}
    raw = p.get("intent_plan")
    if not isinstance(raw, dict):
        return "未识别到多意图检索计划。"
    ip = intent_plan_from_dict(raw)
    if not ip.steps:
        return "未识别到多意图检索计划。"

    sections: List[str] = []
    if len(ip.steps) == 1 and ip.steps[0].chain == CHAIN_EQUIP_PROJECT_METHOD:
        step = ip.steps[0]
        slots = {**step.slots, **step.anchor_slots}
        equip = str(slots.get("focus") or "").strip()
        proj_hints = step.filter_hints.get("project_keywords") or slots.get("project_keywords") or []
        proj_kw = str(proj_hints[0]).strip() if proj_hints else ""
        s2_rows = _rows_for_step(step_results, "s3") or _rows_for_step(step_results, "s2")
        if proj_kw and s2_rows:
            sections.append(format_project_to_method(s2_rows, proj_kw))
        elif s2_rows:
            sections.append(format_project_to_method(s2_rows, proj_kw or equip))
        else:
            sections.append(f"未检索到「{equip}」下「{proj_kw or '项目'}」的试验方法。")
        return "\n\n".join(sections).strip()

    equip = ""
    for st in ip.steps:
        slots = {**st.slots, **st.anchor_slots}
        equip = str(slots.get("focus") or "").strip()
        if equip:
            break
    if not equip:
        eq = (p.get("equipment_keywords") or []) if isinstance(p.get("equipment_keywords"), list) else []
        if eq:
            equip = str(eq[0]).strip()

    sections: List[str] = []
    for i, step in enumerate(ip.steps):
        spec = GOAL_REGISTRY.get(step.goal)
        title = spec.title if spec else step.goal
        rows = _rows_for_intent_step(step_results, i + 1, step)
        if not rows and neo_rows and i == 0:
            rows = neo_rows
        slots = {**step.slots, **step.anchor_slots}
        step_equip = str(slots.get("focus") or equip or "").strip()
        if not rows:
            sections.append(f"## {title}\n\n未检索到相关记录。")
            continue
        if step.goal == "test_projects":
            entries = _collect_testproject_entries(rows)
            names = _unique_project_names(entries)
            if names and step_equip:
                sections.append(f"## {title}\n\n{step_equip}的试验项目：" + "、".join(names))
            else:
                body = format_rel_one_hop(rows, from_type="Equipment", to_type="TestProject", focus=step_equip)
                sections.append(f"## {title}\n\n{body}")
        elif step.goal == "indicators":
            names = _collect_indicator_names(rows)
            if names and step_equip:
                sections.append(f"## {title}\n\n{step_equip}的考核指标：" + "、".join(names))
            else:
                body = format_rel_one_hop(rows, from_type="Equipment", to_type="Indicator", focus=step_equip)
                sections.append(f"## {title}\n\n{body}")
        elif step.goal == "methods":
            proj_hints = step.filter_hints.get("project_keywords") or slots.get("project_keywords") or []
            proj_kw = str(proj_hints[0]).strip() if proj_hints else ""
            body = format_project_to_method(rows, proj_kw or step_equip)
            sections.append(f"## {title}\n\n{body}")
        else:
            body = format_enum_nodes(rows, "TestProject")
            sections.append(f"## {title}\n\n{body}")

    compact: List[str] = []
    for sec in sections:
        parts = sec.split("\n\n", 1)
        if len(parts) == 2 and parts[1].strip():
            compact.append(parts[1].strip())
    if len(compact) >= 2:
        return "；".join(compact) + "。"
    return "\n\n".join(s for s in sections if s).strip() or "未检索到相关记录。"


def format_composite_playbook(
    user_biz: str,
    *,
    neo_rows: List[Dict[str, Any]],
    params: Optional[Dict[str, Any]] = None,
    step_results: Optional[List[Dict[str, Any]]] = None,
) -> str:
    from .user_biz_catalog import (
        COMP_EQUIP_PROJECT_ENV_SETTINGS,
        COMP_INDICATOR_THEN_METHOD,
        COMP_INTENT_PLAN,
        COMP_PROJECT_ENV_METHOD,
        COMP_PROJECT_THEN_METHOD,
    )

    p = params or {}
    if user_biz == COMP_INTENT_PLAN:
        return format_intent_plan_answer(neo_rows=neo_rows, params=p, step_results=step_results)

    equip = str(p.get("focus") or "").strip()
    eq_kws = p.get("equipment_keywords") if isinstance(p.get("equipment_keywords"), list) else []
    if not equip and eq_kws:
        equip = str(eq_kws[0]).strip()
    sections: List[str] = []

    if user_biz in (COMP_PROJECT_THEN_METHOD, COMP_PROJECT_ENV_METHOD):
        rq = str(p.get("retrieval_question") or p.get("question") or "").strip()
        env_lbl = str(p.get("environment_label") or "").strip()
        env_kws = p.get("environment_keywords") if isinstance(p.get("environment_keywords"), list) else []
        if not env_lbl and env_kws:
            env_lbl = str(env_kws[0]).strip()
        has_env = bool(env_lbl) or user_biz == COMP_PROJECT_ENV_METHOD
        proj_rows, method_rows = _resolve_composite_proj_method_rows(
            step_results, neo_rows, has_env=has_env
        )
        return _format_equip_projects_methods_composite(
            equip,
            proj_rows,
            method_rows,
            retrieval_question=rq,
            environment_label=env_lbl,
        )

    if user_biz == COMP_INDICATOR_THEN_METHOD:
        s1_rows = _rows_for_step(step_results, "s1") or neo_rows
        s2_rows = _rows_for_step(step_results, "s2")
        sections.append(
            format_rel_one_hop(
                s1_rows,
                from_type="Equipment",
                to_type="Indicator",
                focus=equip,
            )
        )
        ind_names = _collect_indicator_names(s1_rows)
        if s2_rows and ind_names:
            method_sections: List[str] = []
            for ind in ind_names[:30]:
                methods: List[str] = []
                seen_m: set = set()
                for row in s2_rows:
                    if not isinstance(row, dict) or row.get("error"):
                        continue
                    ind_nm = ""
                    meth_nm = ""
                    for side in ("m", "n"):
                        labs = _node_labels(row, side)
                        if "Indicator" in labs:
                            ind_nm = _node_label(row, side, "Indicator")
                        if "TestMethod" in labs:
                            meth_nm = _node_label(row, side, "TestMethod")
                    if ind_nm != ind:
                        continue
                    if meth_nm and meth_nm not in seen_m:
                        seen_m.add(meth_nm)
                        methods.append(meth_nm)
                if methods:
                    method_sections.append(
                        f"指标「{ind}」关联试验方法：{'、'.join(methods[:15])}"
                    )
            if method_sections:
                sections.append("\n".join(method_sections))
            elif s2_rows:
                methods_all: List[str] = []
                seen_all: set = set()
                for row in s2_rows:
                    for side in ("m", "n"):
                        if "TestMethod" in _node_labels(row, side):
                            nm = _node_label(row, side, "TestMethod")
                            if nm and nm not in seen_all:
                                seen_all.add(nm)
                                methods_all.append(nm)
                if methods_all:
                    head = f"关联试验方法（共 {len(methods_all)} 条）："
                    body = "\n".join(f"- {m}" for m in methods_all)
                    sections.append(f"{head}\n{body}")
        return "\n\n".join(s for s in sections if s).strip() or "未检索到指标或方法。"

    if user_biz == COMP_EQUIP_PROJECT_ENV_SETTINGS:
        scenario = str(p.get("scenario") or "").strip()
        env_kws = p.get("environment_keywords") if isinstance(p.get("environment_keywords"), list) else []
        env_focus = str(env_kws[0]).strip() if env_kws else ""

        if scenario == "by_environment":
            proj_rows = _rows_for_step(step_results, "s2") or _rows_for_step(step_results, "s1") or neo_rows
            env_rows = _rows_for_step(step_results, "s3") or neo_rows
            if env_rows:
                sections.append(
                    format_node_properties(
                        env_rows,
                        node_type="Environment",
                        focus=env_focus,
                    )
                )
            if equip and proj_rows:
                sections.append(
                    format_rel_one_hop(
                        proj_rows,
                        from_type="Equipment",
                        to_type="TestProject",
                        focus=equip,
                    )
                )
            return "\n\n".join(s for s in sections if s).strip() or "未检索到环境设置。"

        s1_rows = _rows_for_step(step_results, "s1") or neo_rows
        s2_rows = _rows_for_step(step_results, "s2b") or _rows_for_step(step_results, "s2")
        s3_rows = _rows_for_step(step_results, "s3")
        if equip:
            sections.append(
                format_rel_one_hop(
                    s1_rows,
                    from_type="Equipment",
                    to_type="TestProject",
                    focus=equip,
                )
            )
        env_names: List[str] = []
        seen_e: set = set()
        for row in s2_rows:
            for side in ("m", "n"):
                if "Environment" in _node_labels(row, side):
                    nm = _node_label(row, side, "Environment")
                    if nm and nm not in seen_e:
                        seen_e.add(nm)
                        env_names.append(nm)
        if env_names:
            sections.append("相关试验环境：\n" + "\n".join(f"- {n}" for n in env_names))
        env_prop_rows = s3_rows if s3_rows else s2_rows
        if env_prop_rows:
            sections.append(
                format_node_properties(
                    env_prop_rows,
                    node_type="Environment",
                    focus=env_names[0] if env_names else "",
                )
            )
        return "\n\n".join(s for s in sections if s).strip() or "未检索到项目或环境设置。"

    return format_enum_nodes(neo_rows, str(p.get("node_type") or ""))


def format_direct_answer(
    business_id: str,
    *,
    neo_rows: List[Dict[str, Any]],
    chroma_rows: List[Dict[str, Any]],
    params: Optional[Dict[str, Any]] = None,
    user_biz: str = "",
    step_results: Optional[List[Dict[str, Any]]] = None,
) -> str:
    p = params or {}
    if user_biz:
        from .user_biz_catalog import COMPOSITE_REGISTRY

        if user_biz in COMPOSITE_REGISTRY:
            return format_composite_playbook(
                user_biz,
                neo_rows=neo_rows,
                params=p,
                step_results=step_results,
            )
    if business_id == "BIZ_PROJECT_TO_METHOD":
        proj_kws = p.get("project_keywords") if isinstance(p.get("project_keywords"), list) else []
        proj_kws = [str(x).strip() for x in proj_kws if str(x).strip()]
        if len(proj_kws) > 1:
            return format_projects_to_method(neo_rows, proj_kws)
        return format_project_to_method(neo_rows, str(p.get("project_name") or proj_kws[0] if proj_kws else ""))
    if business_id == "BIZ_METHOD_CONTENT":
        text, _ = format_method_content(
            neo_rows,
            project_name=str(p.get("project_name") or ""),
            method_name=str(p.get("method_name") or ""),
        )
        return text
    if business_id == "BIZ_DOCUMENT":
        return format_direct_doc_answer(chroma_rows)
    if business_id == "BIZ_ENUM_NODES":
        nt = str(p.get("node_type") or "").strip()
        rid = str(p.get("route_id") or "").strip()
        if not nt and rid == "R-EQUIPMENT-list":
            nt = "Equipment"
        elif not nt and rid == "R-EQUIPMENT_TYPE-level1_list":
            nt = "EquipmentType"
        elif not nt and rid == "R-ENVIRONMENT-list":
            nt = "Environment"
        elif not nt and rid == "R-TEST_PROJECT-enum":
            nt = "TestProject"
        elif not nt and rid == "R-INDICATOR-list_all":
            nt = "Indicator"
        if not nt:
            nt = "Equipment"
        return format_enum_nodes(neo_rows, nt)
    if business_id == "BIZ_NODE_PROP_REV":
        return format_enum_nodes(neo_rows, str(p.get("node_type") or "EquipmentType"))
    if business_id == "BIZ_NODE_PROP":
        eq_kws = p.get("environment_keywords") if isinstance(p.get("environment_keywords"), list) else []
        focus = str(p.get("focus") or p.get("value") or (eq_kws[0] if eq_kws else "")).strip()
        prop_fields = p.get("property_fields") if isinstance(p.get("property_fields"), list) else None
        return format_node_properties(
            neo_rows,
            node_type=str(p.get("node_type") or "Environment"),
            property_fields=prop_fields,
            focus=focus,
        )
    if business_id in ("BIZ_REL_ONE_HOP", "BIZ_REL_REVERSE_ONE_HOP"):
        eq_kws = p.get("equipment_keywords") if isinstance(p.get("equipment_keywords"), list) else []
        env_kws = p.get("environment_keywords") if isinstance(p.get("environment_keywords"), list) else []
        disp_focus = str(p.get("focus") or p.get("focus_id") or "")
        if len(eq_kws) > 1:
            disp_focus = ""
        elif not disp_focus and len(eq_kws) == 1:
            disp_focus = str(eq_kws[0])
        env_lbl = str(p.get("environment_label") or (env_kws[0] if env_kws else "")).strip()
        return format_rel_one_hop(
            neo_rows,
            from_type=str(p.get("from_type") or "Equipment"),
            to_type=str(p.get("to_type") or "TestProject"),
            focus=disp_focus,
            result_type=str(p.get("result_type") or ""),
            where_side=str(p.get("where_side") or ("to" if business_id == "BIZ_REL_REVERSE_ONE_HOP" else "from")),
            anchor_type=str(p.get("anchor_type") or ""),
            environment_label=env_lbl,
        )
    if business_id in ("BIZ_EQUIP_PROJECT_BY_METHOD", "BIZ_REL_TWO_HOP"):
        return format_equip_project_by_method(
            neo_rows,
            method_keyword=str(p.get("method_keyword") or p.get("keyword") or ""),
        )
    if neo_rows:
        return format_enum_nodes(neo_rows, str(p.get("node_type") or ""))
    if chroma_rows:
        return format_direct_doc_answer(chroma_rows)
    return "未检索到可用数据。"
