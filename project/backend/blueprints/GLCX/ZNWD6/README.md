# ZNWD6 — 本体驱动智能问答（Greenfield）

与 **ZNWD4/ZNWD5 隔离**：不 monkey-patch；路由在 ZNWD6 内完成，执行/合成复用 ZNWD4。

## API（与 ZNWD5 对齐，便于前端切换）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/znwd6/chat/stream` | SSE 流式问答 |
| GET | `/api/znwd6/index/status` | 索引与本体状态 |
| GET | `/api/znwd6/ontology` | Ontology Pack 摘要 |
| POST | `/api/znwd6/source/table` | 来源表格补全 |
| POST | `/api/znwd6/feedback` | 答案反馈 |
| GET/DELETE | `/api/znwd6/memory` | 会话记忆 |

## 流水线

```
问句 → τ 高召回探测(Zvec hybrid + Neo4j contains)
     → LLM 判别 binding（σ ⊆ τ）+ template_id
     → plan_verify
     → initial_plan 注入 ZNWD4 reconcile → execute → 合成
```

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `ZNWD6_GROUNDING_SCORE_THRESHOLD` | `0.35` | τ 候选分数阈值 |
| `ZNWD6_GROUNDING_MAX_PER_SPAN` | `16` | 每 mention 最大候选数 |
| `ZNWD6_LLM_BINDING` | `1` | 是否 LLM 消歧（0=Top1 自动） |
| `ZNWD6_USE_ZNWD5_ZVEC` | `1` | 复用 ZNWD5 Zvec 索引 |
| `ZNWD6_ONTOLOGY_DIR` | `./ontology` | 本体 YAML 目录 |
| `ZVEC_DATA_PATH` | `backend/zvec_data` | 向量索引路径 |

LLM 使用现有 vLLM（`OPENAI_BASE_URL` / `OPENAI_MODEL`），无需额外配置。

## 新机迁移

1. 拷贝代码 + `ontology/`（不含业务数据）
2. 配置 `.env`（Neo4j、ZVEC、vLLM）
3. ETL 导入 Neo4j + 重建 Zvec 索引（`ZNWD5/build_znwd5_indexes.py` 或后续 ZNWD6 脚本）
4. 前端设 `NEXT_PUBLIC_ZNWD_CHAT_STREAM=/api/znwd6/chat/stream`

## 与 ZNWD5 区别

| | ZNWD5 | ZNWD6 |
|--|-------|-------|
| 集成 | patch ZNWD4 probe/路由 | **独立路由**，`initial_plan` 注入 |
| 实体 | Top-3 grounding | **高召回 τ + LLM 选** |
| 配置 | 代码内领域规则 | **ontology/*.yaml** |
| 路由 | legacy/action_planner 多模式 | **template_id 单链路** |

## 测试

```bash
cd backend
python -m pytest blueprints/GLCX/ZNWD6/tests -q
python blueprints/GLCX/ZNWD6/migrate_check.py
python blueprints/GLCX/ZNWD6/benchmark/run_znwd6_qa_benchmark.py --output-tag znwd6_200
```
