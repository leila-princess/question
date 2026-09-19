# ZNWD1 智能问答（后端）

独立蓝图，入口 `POST /api/znwd1/chat/stream`。与旧版 `ZNWD` 并行，前端默认走本接口。

**架构说明（推荐阅读）**：[ARCHITECTURE.md](ARCHITECTURE.md) — 四层模型（8 类用户业务 / Playbook / 7 种原子范式 / 2 类元操作）、路由提示词、RoutePlan 格式与全流程图（中文）。

## 流水线

```
用户问句
  → [L0] 打招呼规则 → direct_llm（可选跳过路由）
  → 路由 LLM（UserBiz 8 类 + 场景 + 槽位；可选工具 probe/history）
  → RoutePlan JSON（user_biz + scenario + match_confidence，或 fallback purpose_node + biz_type）
  → plan_reconcile（**Playbook 高置信优先** → 否则 Atomic v2）
  → expand_playbook | resolve_business_from_plan → business_id / meta_ops 多步
  → expand_business → executor（Neo4j / Chroma）
  → 直出 formatters | 合成 LLM 流式
```

## 核心模块

| 文件 | 作用 |
|------|------|
| `user_biz_catalog.py` | 8 类顶层业务 + scenario → business_id / 拼装 Playbook |
| `playbook_resolver.py` | expand_playbook / expand_playbook_meta_steps |
| `prompts/routing_prompt_builder.py` | 路由静态核（**UserBiz 表** + Atomic fallback + 消歧铁律） |
| `routing_disambiguation.py` | 消歧铁律正文（汇总旧版 ZNWD / reconcile / inference） |
| `business_catalog_v2.py` | purpose+biz_type → business_id 查表 |
| `route_resolver.py` | 解析入口 |
| `plan_reconcile.py` | LLM RoutePlan 优先 + guardrail |
| `business_expand.py` | business_id → meta_ops steps |
| `pipeline.py` | SSE 主流水线 |
| `source_table_bridge.py` | 来源表格（复用 ZNWD `source_table`） |
| `znwd1_thinking_emit.py` | 思考区里程碑；检索结果摘要 `kind=retrieval` |

## RoutePlan 字段（路由 LLM 输出）

- `user_biz` / `scenario` / `match_confidence`：顶层业务主路由（high/medium 走 Playbook）
- `params_draft`：`equipment_keywords[]`、`environment_keywords[]`、`project_keywords[]` 等槽位
- **Fallback**：`purpose_node` + `biz_type`（`match_confidence=low` 或 user_biz 为空）
- `purpose_node`：答案实体类型（TestProject / Environment / …）
- `biz_type`：`node_kw` | `node_prop` | `node_prop_rev` | `enum` | `hop_n` | `document`
- `known_type` / `hop_direction`：关系类必填
- `params_draft`：`focus`、`focus_id`、`field`、`property_fields` 等
- 兼容旧字段 `intended_business_id`

## SSE 事件

| 事件 | kind（thinking 时） | 说明 |
|------|---------------------|------|
| `thinking_start` | — | 首包思考区开始 |
| `thinking` | system / route / retrieval / synthesis | 里程碑与流式思考 |
| `search_keywords` | — | `ssyqTableKeyword`，供前端 `/source/table` |
| `source_data` | — | `neo4jResults` / `chromaResults`，供来源抽屉与表格 |
| `answer` | — | 正文（直出或合成） |
| `done` | — | fullAnswer / fullThinking / ssyqTableKeyword |

## 来源表格 API

`POST /api/znwd1/source/table` — 请求体与旧版 `/api/znwd/source/table` 相同（`keyword` / `neo4jHits` / `userQuestion`）。流式回答结束后前端自动调用并缓存，点击「查看来源」直接展示。

程序化检索命中后，思考区会流式输出 `【检索结果】` 摘要（`kind=retrieval`），不经 LLM。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `ZNWD1_INSTANT_THINKING` | 1 | HTTP 层即时首包思考文案 |
| `ZNWD1_ROUTE_MAX_TOOL_ROUNDS` | 2 | 路由工具最大轮次 |
| `ZNWD1_ROUTE_STREAM_THINKING` | 0 | 是否流式展示路由 LLM 原始推理（默认关，仅中文里程碑） |
| `ZNWD1_TOOL_CALL_PARSER` | hermes | vLLM tool_call_parser |

## 验收问句

见 `FLOW.md`。
