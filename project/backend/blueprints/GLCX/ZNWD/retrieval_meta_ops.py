"""
ZNWD V5 元操作定义（Neo4j + Chroma）。

目标：
- 把“可执行能力”从提示词与代码中拆出来，形成可维护的白名单元操作。
- LLM 只能选择 op_code + params，不能任意生成查询语句。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


NODE_LABELS: List[str] = [
    "EquipmentType",
    "Equipment",
    "Environment",
    "TestProject",
    "Indicator",
    "TestMethod",
    "File",
]

REL_TYPES: List[str] = ["HAS", "CON", "BELONGS_TO"]

# 每类节点可查询属性（用于属性级检索与后续扩展）
NODE_ENTITY_FIELDS: Dict[str, List[str]] = {
    "EquipmentType": [
        "Id",
        "Name",
        "Label",
        "Description",
        "Type",
        "Status",
        "User_id",
        "Create_time",
    ],
    "Equipment": [
        "Id",
        "Name",
        "Label",
        "Type",
        "Description",
        "Status",
        "User_id",
        "Create_time",
        "EquipmentTypeId",
    ],
    "Environment": [
        "Id",
        "Name",
        "Label",
        "Description",
        "Type",
        "Class",
        "Combination",
        "Limit",
        "Request",
        "Status",
        "User_id",
        "Create_time",
    ],
    "TestProject": [
        "Id",
        "Name",
        "Type",
        "Label",
        "Description",
        "Status",
        "User_id",
        "Create_time",
        "EnvironmentId",
        "Equipment_name",
    ],
    "Indicator": [
        "Id",
        "Name",
        "Label",
        "Description",
        "Type",
        "Status",
        "User_id",
        "Create_time",
        "Equipment_name",
    ],
    "TestMethod": [
        "Id",
        "Name",
        "Class",
        "Label",
        "Description",
        "SYTJ",
        "SYGC",
        "JGPD",
        "Sy",
        "Source_name",
        "Status",
        "Project_name",
        "Project_rate",
        "Indicator_name",
        "Indicator_rate",
    ],
    "File": [
        "Id",
        "Type",
        "Label",
        "Name",
        "Chroma_id",
        "Chroma_chunks",
        "minIo_id",
        "Image_true",
        "Image_minIO_id",
        "Status",
        "User_id",
        "Create_time",
    ],
}

# 兼容旧引用：所有节点属性去重后的并集
ENTITY_FIELDS: List[str] = sorted({f for fields in NODE_ENTITY_FIELDS.values() for f in fields})

# 字段语义与特殊取值说明（供程序生成与后续维护）
FIELD_EXPLANATIONS: Dict[str, Dict[str, Any]] = {
    "TestProject": {
        "Type": {
            "meaning": "项目层级类型",
            "values": ["test", "category"],
            "notes": "test=子级具体试验项目；category=父级分类项目",
        },
        "Equipment_name": {
            "meaning": "关联设备名称（冗余/派生字段）",
            "notes": "可用于快速按设备名过滤项目，不替代图关系查询",
        },
    },
    "Indicator": {
        "Equipment_name": {
            "meaning": "关联设备名称（冗余/派生字段）",
            "notes": "可用于快速按设备名过滤指标，不替代图关系查询",
        },
    },
    "TestMethod": {
        "Project_name": {"meaning": "关联项目名称"},
        "Project_rate": {"meaning": "与项目关联度（数值越大关联越强）"},
        "Indicator_name": {"meaning": "关联指标名称"},
        "Indicator_rate": {"meaning": "与指标关联度（数值越大关联越强）"},
    },
}

# 关系语义说明（方向固定）
RELATION_EXPLANATIONS: List[Dict[str, str]] = [
    {"from": "EquipmentType", "rel": "HAS", "to": "Equipment", "meaning": "设备类别包含设备"},
    {"from": "Equipment", "rel": "CON", "to": "TestProject", "meaning": "设备关联测试项目"},
    {"from": "Environment", "rel": "CON", "to": "TestProject", "meaning": "环境关联测试项目"},
    {"from": "Equipment", "rel": "CON", "to": "Indicator", "meaning": "设备关联指标"},
    {"from": "TestProject", "rel": "CON", "to": "TestMethod", "meaning": "项目关联测试方法"},
    {"from": "Indicator", "rel": "CON", "to": "TestMethod", "meaning": "指标关联测试方法"},
    {"from": "TestMethod", "rel": "BELONGS_TO", "to": "File", "meaning": "方法关联来源文件"},
    {"from": "TestProject", "rel": "HAS", "to": "TestProject", "meaning": "父项目包含子项目"},
]

RELATION_SCHEMA: Dict[str, List[Tuple[str, str]]] = {
    "CON": [
        ("Equipment", "TestProject"),
        ("Environment", "TestProject"),
        ("Equipment", "Indicator"),
        ("TestProject", "TestMethod"),
        ("Indicator", "TestMethod"),
    ],
    "HAS": [
        ("EquipmentType", "Equipment"),
        ("TestProject", "TestProject"),
    ],
    "BELONGS_TO": [
        ("TestMethod", "File"),
    ],
}


META_OPS: Dict[str, Dict[str, Any]] = {
    "neo_node_query": {
        "kind": "neo4j",
        "description": (
            "单标签节点模糊查询：MATCH (n:Label) WHERE toLower(coalesce(n.field,'')) CONTAINS toLower($value) RETURN n。"
            "用于先「锁定一类实体」再交给下一步关系查询。常见："
            "① TestProject + Name：用户说「高温测试」「淋雨试验」「XX鉴定」等以试验/项目名义提问时，用 node_type=TestProject、field=Name、value 填核心短词（如「高温」「淋雨」），不要把整句「高温测试」硬塞给 Equipment；"
            "② Environment + Name：高温、低温、湿热、盐雾、振动等环境词；问环境分类/分级/组合/约束/要求时用 neo_list_nodes(Environment) 或 neo_node_query 锁定后读节点属性 Type、Class、Combination、Limit、Request（勿用 TestProject）；"
            "③ Equipment + Name：坦克、无人机等具体装备名。"
        ),
        "params_schema": {
            "node_type": {"type": "enum", "required": True, "values": NODE_LABELS},
            "field": {"type": "string", "required": False, "default": "Name"},
            "value": {"type": "string", "required": True},
            "limit": {"type": "int", "required": False, "default": 50, "min": 1, "max": 300},
        },
    },
    "neo_list_nodes": {
        "kind": "neo4j",
        "description": (
            "按节点标签列举库中该类型的节点（用于「有哪些设备类型/列出全部环境/枚举所有指标」等枚举类问题，"
            "不要求关键词）。可选仅返回启用状态（Status）的节点。"
            "问「环境如何分类/分级/典型组合/约束/要求」或环境体系时，对 node_type=Environment 列举后从各节点 "
            "Type、Class、Combination、Limit、Request 属性归纳（字段名与库一致）。"
        ),
        "params_schema": {
            "node_type": {"type": "enum", "required": True, "values": NODE_LABELS},
            "limit": {"type": "int", "required": False, "default": 200, "min": 1, "max": 500},
            "status_filter": {
                "type": "int",
                "required": False,
                "default": 1,
                "min": 0,
                "max": 1,
            },
        },
    },
    "neo_relation_query": {
        "kind": "neo4j",
        "description": (
            "单跳关系查询 MATCH (n:from_type)-[r:rel_type]->(m:to_type)。"
            "match_mode=fuzzy 时对起点 n 的 from_field 做子串匹配；match_mode=in 时用 from_values_ref 引用上一步结果中节点的 Id 列表（通常 target 为 n 或 m，field 为 Id）。"
            "查「某项目关联的测试方法」：from_type=TestProject, rel_type=CON, to_type=TestMethod, match_mode=in, from_values_ref 指向前一步 neo_node_query 或上一步返回的 TestProject 节点。"
            "查「某装备下的项目」：from_type=Equipment, rel_type=CON, to_type=TestProject, match_mode=fuzzy, value=装备名。"
            "查「某环境下的项目」：from_type=Environment, rel_type=CON, to_type=TestProject。"
            "查「哪些设备/装备具备某类测试项目（名称含高温、淋雨等）」：from_type=Equipment, rel_type=CON, to_type=TestProject, "
            "match_mode=fuzzy, value=项目名核心词（如「高温」），**where_side=to**（WHERE 作用在终点 m 即 TestProject.Name，起点 n 为设备；不要用 neo_node_query(TestProject) 再口述推断设备）。"
            "match_mode=in 时若用 from_values_ref 引用上一步节点 Id，同样可用 where_side=to 表示 Id 列表属于 TestProject 端。"
        ),
        "cypher_template": (
            "MATCH (n:{node1})-[r:{rel}]->(m:{node2}) "
            "WHERE {where_clause} "
            "RETURN n, r, m LIMIT $limit"
        ),
        "params_schema": {
            "from_type": {"type": "enum", "required": True, "values": NODE_LABELS},
            "rel_type": {"type": "enum", "required": True, "values": REL_TYPES},
            "to_type": {"type": "enum", "required": True, "values": NODE_LABELS},
            "from_field": {"type": "string", "required": False, "default": "Name"},
            "match_mode": {"type": "enum", "required": False, "default": "fuzzy", "values": ["fuzzy", "in"]},
            "where_side": {
                "type": "enum",
                "required": False,
                "default": "from",
                "values": ["from", "to"],
            },
            "value": {"type": "string", "required": False},
            "limit": {"type": "int", "required": False, "default": 30, "min": 1, "max": 500},
        },
    },
    "neo_property_filter": {
        "kind": "neo4j",
        "description": "属性过滤：对上一步结果按属性做模糊子串过滤。",
        "params_schema": {
            "target": {"type": "enum", "required": False, "default": "m", "values": ["n", "m", "r"]},
            "field": {"type": "string", "required": True},
            "value": {"type": "string", "required": True},
        },
    },
    "chroma_vector_search": {
        "kind": "chroma",
        "description": "向量召回文档片段（可按 doc_id 或 title 过滤）。",
        "params_schema": {
            "query": {"type": "string", "required": True},
            "top_k": {"type": "int", "required": False, "default": 12, "min": 1, "max": 200},
            "doc_id": {"type": "string", "required": False},
            "title_keyword": {"type": "string", "required": False},
        },
    },
    "chroma_score_filter": {
        "kind": "chroma",
        "description": "按相似度/距离阈值过滤候选片段。",
        "params_schema": {
            "min_score": {"type": "float", "required": False, "default": 0.0, "min": -1.0, "max": 2.0},
            "max_distance": {"type": "float", "required": False, "default": 2.0, "min": 0.0, "max": 10.0},
        },
    },
    "chroma_rerank": {
        "kind": "chroma",
        "description": "基于关键词覆盖和长度启发式重排序（预留后续替换为 cross-encoder/LLM rerank）。",
        "params_schema": {
            "query": {"type": "string", "required": True},
            "top_k": {"type": "int", "required": False, "default": 12, "min": 1, "max": 200},
        },
    },
    "chroma_section_expand": {
        "kind": "chroma",
        "description": "按章节前缀扩展：如 1.1 -> 1.1、1.1.1、1.1.1.1 ... 并按章节顺序返回。",
        "params_schema": {
            "doc_id": {"type": "string", "required": False},
            "title_keyword": {"type": "string", "required": False},
            "section_prefix": {"type": "string", "required": True},
            "max_chunks": {"type": "int", "required": False, "default": 200, "min": 1, "max": 2000},
        },
    },
}


def meta_ops_for_prompt() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for code, cfg in META_OPS.items():
        rows.append(
            {
                "op_code": code,
                "kind": cfg.get("kind"),
                "description": cfg.get("description", ""),
                "params_schema": cfg.get("params_schema", {}),
                "node_entity_fields": NODE_ENTITY_FIELDS,
                "field_explanations": FIELD_EXPLANATIONS,
                "relation_explanations": RELATION_EXPLANATIONS,
                "relation_schema": RELATION_SCHEMA,
            }
        )
    return rows


# V6 智能体唯一检索工具名（与旧名 znwd_execute_meta_steps 等价，服务端双注册）
META_OPS_RETRIEVE_TOOL_NAME = "znwd_meta_ops_retrieve"


def build_znwd_meta_ops_retrieve_tool_definition(
    *,
    max_desc_per_op: int = 360,
    max_total_desc: int = 12000,
) -> Dict[str, Any]:
    """
    构建 OpenAI Chat Completions 用的 function tool 定义：仅允许 META_OPS 中的 op_code，
    载荷为根对象下的 `steps` 数组（与 _execute_meta_steps 一致）。
    """
    lines: List[str] = []
    for code, cfg in META_OPS.items():
        d = (cfg.get("description") or "").replace("\n", " ").strip()
        if len(d) > max_desc_per_op:
            d = d[: max_desc_per_op - 1] + "…"
        lines.append(f"- **{code}**（{cfg.get('kind')}）: {d}")
    body = "\n".join(lines)
    intro = (
        "【ZNWD 元操作检索】唯一合法入口：根据 `backend/blueprints/GLCX/ZNWD/retrieval_meta_ops.py` 中的 **META_OPS** "
        "白名单执行 Neo4j / Chroma 检索。每一步的 **op_code** 必须取自下方列表之一；**params** 的键与取值须满足该 op 的 **params_schema**（系统消息里会注入精简版 meta_ops）。\n"
        "**调用方式**：输出**一个** JSON 对象，根上**仅有**字段 `steps`（数组，1～14 个元素）。每个元素为对象：`op_code`（字符串）、`params`（对象）；可选 `step_id`（建议 s1,s2,…）、`purpose`。\n"
        "**重要**：`steps` 不是摆设；服务端会**逐项真实执行**——`neo_*` 在 Neo4j 跑 Cypher，`chroma_*` 调向量库。\n"
        "**方法类问题**：问「测试方法 / 试验方法 / …的方法」且要查图里的 **TestMethod** 时，**必须先安排 neo_***（如 TestProject→TestMethod），**再**安排 `chroma_vector_search` 用方法名/项目名等构造 query；**不要**整条只有 chroma_*。\n"
        "多步关系查询：若某步为 `neo_relation_query` 且 `match_mode` 为 `in`，须设置 `from_values_ref` 指向上一步（如 `{\"step_id\":\"s1\",\"target\":\"n\",\"field\":\"Id\"}`，与上一步 RETURN 的变量一致）。\n"
        "**禁止**在第一个 JSON 对象闭合后再用逗号拼接第二个根对象（会被解析丢弃）。\n\n"
        "**META_OPS 白名单与用途摘要：**\n"
    )
    desc = intro + body
    if len(desc) > max_total_desc:
        desc = desc[: max_total_desc - 24] + "\n...(description truncated)"
    op_enum = list(META_OPS.keys())
    return {
        "type": "function",
        "function": {
            "name": META_OPS_RETRIEVE_TOOL_NAME,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 14,
                        "description": "顺序执行的元操作步；每项对应 META_OPS 之一",
                        "items": {
                            "type": "object",
                            "properties": {
                                "step_id": {
                                    "type": "string",
                                    "description": "可选；建议 s1,s2,… 便于 from_values_ref 引用",
                                },
                                "op_code": {
                                    "type": "string",
                                    "enum": op_enum,
                                    "description": "必须为 META_OPS 中的键",
                                },
                                "params": {
                                    "type": "object",
                                    "description": "该 op 的参数，字段见 params_schema",
                                },
                                "purpose": {"type": "string", "description": "可选，中文简述本步意图"},
                            },
                            "required": ["op_code", "params"],
                        },
                    },
                    "note": {"type": "string", "description": "可选，与八步思考一致的一句备注"},
                },
                "required": ["steps"],
            },
        },
    }


def clamp_meta_op_params(op_code: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """将整数参数钳制到 params_schema 的 min/max，避免 LLM 略超上限导致整步被丢弃。"""
    out = dict(params or {})
    if op_code == "chroma_score_filter" and "threshold" in out:
        if "min_score" not in out or out.get("min_score") in (None, "", 0, 0.0):
            try:
                out["min_score"] = float(out.get("threshold"))
            except (TypeError, ValueError):
                pass
        out.pop("threshold", None)
    cfg = META_OPS.get(op_code)
    if not cfg:
        return out
    for k, s in (cfg.get("params_schema") or {}).items():
        if s.get("type") != "int" or k not in out:
            continue
        raw = out[k]
        try:
            if isinstance(raw, bool):
                v = int(raw)
            elif isinstance(raw, float) and raw == int(raw):
                v = int(raw)
            elif isinstance(raw, int):
                v = raw
            else:
                v = int(raw)
        except (TypeError, ValueError):
            continue
        if "min" in s:
            v = max(int(s["min"]), v)
        if "max" in s:
            v = min(int(s["max"]), v)
        out[k] = v
    return out


def validate_meta_op(op_code: str, params: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    cfg = META_OPS.get(op_code)
    if not cfg:
        return [f"unknown op_code: {op_code}"]
    schema = cfg.get("params_schema", {})
    for k, s in schema.items():
        if s.get("required") and (k not in params or params.get(k) in (None, "")):
            errs.append(f"missing required param: {k}")
            continue
        if k not in params:
            continue
        v = params.get(k)
        t = s.get("type")
        if t == "enum":
            if v not in s.get("values", []):
                errs.append(f"param {k} must be one of {s.get('values')}")
        elif t == "int":
            if isinstance(v, bool):
                v = int(v)
            elif isinstance(v, float) and v == int(v):
                v = int(v)
            if not isinstance(v, int):
                errs.append(f"param {k} must be int")
                continue
            if "min" in s and v < s["min"]:
                errs.append(f"param {k} < {s['min']}")
            if "max" in s and v > s["max"]:
                errs.append(f"param {k} > {s['max']}")
        elif t == "float":
            if not isinstance(v, (int, float)):
                errs.append(f"param {k} must be float")
                continue
            fv = float(v)
            if "min" in s and fv < s["min"]:
                errs.append(f"param {k} < {s['min']}")
            if "max" in s and fv > s["max"]:
                errs.append(f"param {k} > {s['max']}")
        elif t == "string":
            if not isinstance(v, str):
                errs.append(f"param {k} must be string")
    if op_code == "neo_relation_query" and not errs:
        fr = str(params.get("from_type") or "")
        to = str(params.get("to_type") or "")
        rel = str(params.get("rel_type") or "")
        allowed = RELATION_SCHEMA.get(rel, [])
        if (fr, to) not in allowed:
            errs.append(f"illegal relation triple: {fr}-[{rel}]->{to}")
        mode = str(params.get("match_mode") or "fuzzy")
        has_ref = isinstance(params.get("from_values_ref"), dict)
        has_value = bool(str(params.get("value") or "").strip())
        ws = str(params.get("where_side") or "from").lower()
        if ws not in ("from", "to"):
            errs.append("neo_relation_query where_side must be from|to")
        if mode == "in":
            if not has_ref and not isinstance(params.get("value_list"), list):
                errs.append("neo_relation_query with match_mode=in requires from_values_ref or value_list")
        else:
            if not has_value and not has_ref:
                errs.append("neo_relation_query requires value or from_values_ref")
    return errs
