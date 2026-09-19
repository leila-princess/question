# ZNWD2 智能问答（意图驱动 + 本体路径规划）

独立蓝图，与 ZNWD / ZNWD1 并行。

## HTTP 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/znwd2/chat/stream` | SSE 流式问答（对齐 ZNWD1 事件协议） |
| POST | `/api/znwd2/chat` | 同步 JSON `{ "answer": "..." }`（调试） |

请求体示例：

```json
{
  "question": "无人机有哪些考核指标？",
  "sessionId": "optional",
  "userId": "optional",
  "llmModelKey": "optional"
}
```

## 流水线

```
用户问句
  → intent_parser（LLM → Intent JSON）
  → path_planner（宏优先 / Dijkstra 线性路径 → 动作序列）
  → executor（Neo4j Cypher）
  → result_formatter（自然语言直出）
```

## 核心模块

| 文件 | 作用 |
|------|------|
| `intent_parser.py` | LLM 意图解析 |
| `ontology.py` | NetworkX 模式图 + 代价模型 |
| `path_planner.py` | 宏匹配 + Dijkstra 路径规划 |
| `action_macros.py` | 高频业务 Cypher 宏（指标仅设备；项目含环境双约束） |
| `executor.py` | 动作原语执行引擎 |
| `main.py` | 同步 `answer()` |
| `pipeline.py` | SSE 流式编排 |

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | 见 `.env` | 图数据库 |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` | 见 `.env` | 意图解析 LLM |
| `ZNWD2_INSTANT_THINKING` | 1 | HTTP 层即时首包思考 |
| `ZNWD2_INTENT_MODEL` | 空 | 覆盖意图解析模型 |
| `ZNWD2_INTENT_TEMPERATURE` | 0.1 | 意图解析温度 |
| `ZNWD2_QUERY_LIMIT` | 200 | 单次检索上限 |

## 验收问句

见 [FLOW.md](FLOW.md)。

## 单元测试

```bash
cd backend
python -m unittest discover -s blueprints/GLCX/ZNWD2/tests -p "test_*.py"
```

## 前端切换（可选）

在 `.env.local` 设置：

```
NEXT_PUBLIC_ZNWD_CHAT_STREAM=/api/znwd2/chat/stream
```

并添加 Next.js 代理 `fronted/app/api/znwd2/chat/stream/route.ts`（参考 znwd1）。
