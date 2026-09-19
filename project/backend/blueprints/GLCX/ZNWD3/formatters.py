"""
formatters：检索结果 → 可直接下发的 Markdown 答案（database_direct）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

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
            nm = _node_label(row, side)
            labels = _node_labels(row, side)
            if "TestMethod" in labels or (nm and side == "m"):
                if nm and nm not in seen:
                    seen.add(nm)
                    methods.append(nm)
    if not methods:
        return f"未在清单库中找到测试项目「{project_name}」关联的试验方法，请核对项目名称或换一种问法。"
    lines = [f"测试项目「{project_name}」关联的试验方法如下（共 {len(methods)} 条）：", ""]
    for i, m in enumerate(methods, 1):
        lines.append(f"{i}. {m}")
    return "\n".join(lines)


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
    else:
        head = f"{zh}（共 {len(names)} 条，展示前 {min(len(names), 50)} 条）："
    body = "\n".join(f"- {n}" for n in names[:50])
    return f"{head}\n{body}"


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

    names: List[str] = []
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
        if ws == "to":
            return f"未找到具备{anchor_zh}「{focus}」的{result_zh}。"
        return f"未找到「{focus}」关联的{to_zh}。"
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
    if from_groups:
        total = len(entries)
        fr = from_zh or (node_type_zh(from_type) if from_type else "实体")
        env_lbl = (environment_label or "").strip()
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
    for k in keys:
        v = str(props.get(k) or "").strip()
        if v:
            return v
    return ""


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
            blocks.append(f"### {name}\n\n{synr.strip()}")
            continue

        parts = [f"### {name}", ""]
        has_section = False
        if sytj:
            has_section = True
            parts.extend(["**（一）试验条件**", "", sytj, ""])
        if sygc:
            has_section = True
            parts.extend(["**（二）试验过程**", "", sygc, ""])
        if jgpd:
            has_section = True
            parts.extend(["**（三）结果评定**", "", jgpd, ""])
        if has_section:
            any_content = True
            blocks.append("\n".join(parts).strip())
        else:
            desc = _field_text(props, "Description")
            if desc:
                any_content = True
                blocks.append(f"### {name}\n\n{desc}")

    if not any_content:
        subject = method_name or project_name or methods[0].get("name") or "该方法"
        return f"已找到试验方法「{subject}」，但图库中 SYNR / SYTJ / SYGC / JGPD 均为空，需检索标准文档补充。", False

    head = ""
    if project_name:
        head = f"测试项目「{project_name}」关联的试验方法内容如下：\n\n"
    elif method_name:
        head = f"试验方法「{method_name}」内容如下：\n\n"
    return head + "\n\n".join(blocks), True


def format_direct_answer(
    business_id: str,
    *,
    neo_rows: List[Dict[str, Any]],
    chroma_rows: List[Dict[str, Any]],
    params: Optional[Dict[str, Any]] = None,
) -> str:
    p = params or {}
    if business_id == "BIZ_PROJECT_TO_METHOD":
        return format_project_to_method(neo_rows, str(p.get("project_name") or ""))
    if business_id == "BIZ_METHOD_CONTENT":
        text, _ = format_method_content(
            neo_rows,
            project_name=str(p.get("project_name") or ""),
            method_name=str(p.get("method_name") or ""),
        )
        return text
    if business_id == "BIZ_ENUM_NODES":
        return format_enum_nodes(neo_rows, str(p.get("node_type") or "EquipmentType"))
    if business_id == "BIZ_NODE_PROP_REV":
        return format_enum_nodes(neo_rows, str(p.get("node_type") or "EquipmentType"))
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
    return "未检索到可用数据。"
