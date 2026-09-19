# ZNWD1 智能问答流程

独立后端包，**不 import ZNWD**。与现网 `/api/znwd/chat/stream_v4` 并行。

分层架构、路由提示词与 RoutePlan 契约见 **[ARCHITECTURE.md](ARCHITECTURE.md)**。

## HTTP

- 后端：`POST /api/znwd1/chat/stream`
- 前端代理：`POST /api/znwd1/chat/stream` → Flask 同上

## 流水线

```
用户问句
  → LangChain 路由 Agent（工具：history_recent / history_similar / probe_keyword，≤2 轮）
  → RoutePlan JSON（user_biz + scenario，或 fallback purpose_node + biz_type）
  → plan_reconcile（**Playbook 高置信优先** → Atomic v2；guardrail + 兜底）
  → expand_playbook_meta_steps | business_expand → executor（Neo4j + Chroma；拼装多步合并证据后一次合成）
  → database_direct 直出 | LangChain 合成流式
  → Redis znwd1: 前缀记忆
```

## 思考过程 SSE

| 事件 | 说明 |
|------|------|
| `thinking_start` | 首包（HTTP 层可提前刷出） |
| `thinking` | 里程碑 + 路由/合成 `<THINK>` 流式短句 |
| `answer` | 正文（合成 `<CONTENT>` 或直出） |
| `done.fullThinking` | 思考区全文，前端标记完成 |

首包文案：`已收到您的问题，正在分析…`（`ZNWD1_INSTANT_THINKING=1` 默认开启）。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `ZNWD1_INSTANT_THINKING` | 1 | 首包即时思考里程碑 |
| `ZNWD1_ROUTE_MAX_TOOL_ROUNDS` | 2 | 路由最大工具轮 |
| `ZNWD1_TOOL_CALL_PARSER` | hermes | LangChain extra_body.tool_call_parser |
| `ZNWD1_KEYWORD_LABELS_FAST` | TestProject,Environment,Equipment | probe 优先标签 |

## 验收问句

| 问句 | 期望 |
|------|------|
| 你好 | direct_llm，不调 Neo |
| 坦克有哪些试验项目 | user_biz=UBIZ_TEST_PROJECT, scenario=by_equipment |
| 坦克有哪些试验项目和方法 | user_biz=COMP_PROJECT_THEN_METHOD（两跳+一次合成） |
| 飞行振动测试的试验方法是什么 | UBIZ_TEST_METHOD/by_project 或 Atomic BIZ_PROJECT_TO_METHOD |
| 电池高温测试有哪些方法 | 项目名 AND 分词；命中正确项目 |
| GB/T xxx 章节试验过程 | BIZ_DOCUMENT + Chroma |
| XX方法的具体内容是什么 | BIZ_METHOD_CONTENT：先 TestMethod.SYNR，空则 Chroma |

## 提示词文件（ZNWD1）

| 文件 | 用途 |
|------|------|
| `prompts/routing_agent.py` | 路由 Agent 系统提示（含 **graph_schema 精简本体**） |
| `prompts/graph_schema.py` | 从 `meta_ops.py` **自动生成**节点/关系 Markdown |
| `prompts/synthesis.py` | 合成阶段系统提示（含 **完整本体** + SYNR 规则） |
| `pipeline.py` 内 `_DIRECT_LLM_SYSTEM` | 闲聊/直答短提示 |

本体单一数据源：`meta_ops.py`（`NODE_ENTITY_FIELDS`、`RELATION_EXPLANATIONS`）→ `prompts/graph_schema.py` 生成注入，**勿**手改提示词里的表与 `meta_ops` 不一致。

旧版 ZNWD 提示词仍在 `backend/blueprints/GLCX/ZNWD/prompts_v4.py` ~ `prompts_v6.py`（ZNWD1 **不引用**）。
| 它有哪些试验方法（有上文） | 路由调 history_recent 补全 keyword |
