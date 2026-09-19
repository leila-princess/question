# ZNWD4 智能问答（后端）

独立蓝图，入口 `POST /api/znwd4/chat/stream`。与 `ZNWD1` 并行，**不修改 ZNWD1**；前端可通过 `/api/znwd4/chat/stream` 代理切换。

**架构说明（推荐阅读）**：[ARCHITECTURE.md](ARCHITECTURE.md)

## ZNWD1 与 ZNWD4 的区别

| 维度 | ZNWD1 | ZNWD4 |
|------|-------|-------|
| **路由 LLM 轮次** | 单轮：一次输出完整 RoutePlan（user_biz + scenario + 槽位） | **两轮**：Step1 判 purpose → Step2 选 `route_id` |
| **路由提示词** | UserBiz 8 类大表 + Atomic fallback 同屏 | Step1 目的枚举；Step2 仅注入 **purpose 过滤后的候选 route 表** |
| **关键词→节点** | LLM 槽位 + probe 工具 + reconcile 推断 | **程序 Step3**：强制 probe、`validate_and_align_route`、槽位合并；LLM 不决定 node_type |
| **对外路由 ID** | `user_biz` + `scenario` | 统一 **`route_id`**（如 `R-EQUIPMENT_TYPE-belongs_to`），内部映射到 playbook |
| **plan_reconcile** | 完整 guardrail + indicator 兜底 | v2 高置信 `route_id` 时 **跳过 indicator 兜底与部分 guardrail** |
| **执行层** | expand_playbook → executor → formatters | **相同**（复用 ZNWD1 拷贝的 expand/executor） |
| **历史补全** | `enrich_short_follow_up` | **相同** |
| **来源表格** | `/api/znwd1/source/table` | `/api/znwd4/source/table` |

### 分步路由流程（ZNWD4 独有）

```
用户问句
  → enrich_short_follow_up（历史补全，不变）
  → Step1 LLM：purpose + keywords（可调用 history 工具）
  → 程序：filter_routes_by_purpose → 候选表 markdown
  → Step2 LLM：route_id + params_draft（可调用 znwd4_probe_keyword）
  → Step3 程序：ensure_probe_snapshot → validate_and_align_route → normalize_to_legacy_plan
  → plan_reconcile（playbook 优先，v2 精简 guardrail）
  → expand_playbook → execute → 直出/合成
```

## 流水线

```
用户问句
  → [L0] 打招呼 / R-CHAT-direct → direct_llm
  → 分步路由（见上）
  → plan_reconcile → expand_playbook | Atomic v2
  → expand_business → executor（Neo4j / Chroma）
  → 直出 formatters | 合成 LLM 流式
```

## 核心模块

| 文件 | 作用 |
|------|------|
| `routing_catalog.py` | `route_id` 目录、purpose 过滤、`resolve_route` |
| `route_plan_v2.py` | Step1/2 JSON 解析、legacy plan 归一化 |
| `route_validator.py` | Step3 probe + 对齐 + `assemble_legacy_plan` |
| `prompts/routing_prompt_builder.py` | Step1/Step2 分步提示词 |
| `znwd4_langchain_routing.py` | 分步路由 SSE 流 |
| `plan_reconcile.py` | playbook 优先 + v2 精简 guardrail |
| `pipeline.py` | SSE 主流水线 |
| `source_table_bridge.py` | 来源表格（复用 ZNWD `source_table`） |

## RoutePlan 字段（Step3 输出，兼容 ZNWD1 reconcile）

- `route_id`：如 `R-EQUIPMENT_TYPE-belongs_to`、`R-CHAT-direct`
- `user_biz` / `scenario` / `match_confidence`：由 `resolve_route` 填充
- `params_draft`：槽位 + probe 写入的 `focus` / `focus_id`
- `probe_snapshot`：图谱 probe 结果
- `answer_strategy`：`direct_llm` | `db_direct` | `db_synthesize`

## SSE 事件

| 事件 | kind（thinking 时） | 说明 |
|------|---------------------|------|
| `thinking_start` | — | 首包思考区开始 |
| `thinking` | system / route / retrieval / synthesis | 里程碑与流式思考 |
| `search_keywords` | — | `ssyqTableKeyword` |
| `source_data` | — | Neo/Chroma 命中 |
| `answer` | — | 正文 |
| `done` | — | fullAnswer / fullThinking / ssyqTableKeyword |

## 来源表格 API

`POST /api/znwd4/source/table` — 与 `/api/znwd1/source/table` 相同请求体。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `ZNWD4_INSTANT_THINKING` | 1 | HTTP 层即时首包思考文案 |
| `ZNWD4_ROUTE_MAX_TOOL_ROUNDS` | 2 | 路由工具最大轮次 |
| `ZNWD4_ROUTE_STREAM_THINKING` | 0 | 是否流式展示路由 LLM 原始推理 |
| `ZNWD4_TOOL_CALL_PARSER` | hermes | vLLM tool_call_parser |
| `ZNWD4_LLM_TIMEOUT` | 180 | LangChain 请求超时（秒） |
| `ZNWD4_KEYWORD_LABELS_FAST` | TestProject,Environment,Equipment | probe 优先标签 |

## 验收问句

见 `FLOW.md`。

## 单测

```bash
cd backend
python -m unittest blueprints.GLCX.ZNWD4.test_routing_v2 -v
```
