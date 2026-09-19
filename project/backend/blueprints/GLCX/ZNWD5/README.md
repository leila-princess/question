# ZNWD5：Zvec 混合检索智能问答 POC

## 入口

- `POST /api/znwd5/chat/stream` — SSE 流式问答（与 ZNWD4 格式一致）
- `GET /api/znwd5/index/status` — Zvec 索引状态

## 架构

继承 **ZNWD4** 全部路由/Playbook/合成逻辑，仅替换：

1. **实体 probe**：`keyword_probe` → Zvec BGE 语义召回 + CONTAINS 降级
2. **文档检索**：`chroma_*` → `zvec_doc_*`（向量 + FTS 混合，默认开启）

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `ZVEC_DATA_PATH` | `backend/zvec_data` | Zvec 索引目录 |
| `ZNWD5_DOC_ENGINE` | `zvec` | `zvec` / `chroma` |
| `ZNWD5_ZVEC_PROBE` | `1` | 是否启用语义 probe |
| `ZNWD5_ROUTING_MODE` | `legacy` | `legacy` 走 ZNWD4 路由；`action_planner` 启用动作规划器 |
| `ZNWD5_PLANNER_SKIP_LOCKS` | `0` | `1` 时强制全问句走 LLM 规划（跳过 chat/meta/enum 快路径） |
| `ZNWD5_GROUNDING_TOPK` | `3` | Zvec grounding TopK |
| `ZNWD5_ACTION_MAX_STEPS` | `6` | ActionPlan 最大步数 |
| `ZNWD5_NEO_VECTOR_ENABLED` | `0` | Neo4j 原生向量 A/B |
| `ZVEC_EMBEDDING_MODEL` | BGE 路径 | 嵌入模型目录 |

## 前置条件

- **Python 3.10+** 安装 `zvec`（主环境 Python 3.9 将自动降级为 numpy fallback）
- Neo4j、BGE 模型（`bge-small-zh-v1.5`）与 Chroma 数据可达

## 动作规划器（action_planner）

并行模式，与 legacy 共存：

```powershell
$env:ZNWD5_ROUTING_MODE = "action_planner"
```

流程：Zvec Top3 grounding → LLM 一次输出 ActionPlan（7 动作 + 7 节点）→ 校验 → `business_expand` 展开 → executor 链式执行 → 拼装/合成。

快路径仍走 legacy：`R-CHAT-direct`、`R-META-*`、无槽 enum 程序锁。

A/B 评测：

```powershell
cd F:\antd-demo1\backend
python blueprints/GLCX/ZNWD5/benchmark/run_action_planner_ab_benchmark.py --limit 20
```

## 索引构建

**首次 / 完整重建**（须先停 Flask）：

```powershell
cd backend
python blueprints/GLCX/ZNWD5/build_znwd5_indexes.py --force
```

**日常一键更新**（Neo4j / Chroma 变化后，须先停 Flask）：

```powershell
cd backend
python blueprints/GLCX/ZNWD5/update_znwd5_indexes.py          # auto：有变化才更新
python blueprints/GLCX/ZNWD5/update_znwd5_indexes.py --dry-run # 仅看计划
python blueprints/GLCX/ZNWD5/update_znwd5_indexes.py --watch 300 # 每 5 分钟检测
```

或 PowerShell：`.\06241\scripts\update_znwd5_indexes.ps1`

| 模式 | 行为 |
|------|------|
| `auto`（默认） | 对比快照：无变化跳过；纯新增/内容变更→增量 upsert；有删除→重建 |
| `incremental` | 不删索引，全量 upsert |
| `full` | 删除旧目录后完整重建 |

已有索引首次启用 auto 模式：`python blueprints/GLCX/ZNWD5/update_znwd5_indexes.py --init-state`（Flask 运行中也可，只写快照）

快照文件：`backend/zvec_data/index_update_state.json`

分步脚本（旧）：

```powershell
python blueprints/GLCX/ZNWD5/sync_neo4j_to_zvec.py
python blueprints/GLCX/ZNWD5/sync_chroma_to_zvec.py --re-embed
python blueprints/GLCX/ZNWD5/phase0_stats.py
```

## Benchmark

```powershell
python blueprints/GLCX/ZNWD5/benchmark/run_znwd5_qa_benchmark.py --limit 20
```

## 与 ZNWD4 对比

| 能力 | ZNWD4 | ZNWD5 |
|------|-------|-------|
| 实体探测 | Neo4j CONTAINS | Zvec 语义 + CONTAINS |
| 文档检索 | Chroma + MiniLM | Zvec + BGE + FTS |
| 图遍历 | neo_* 元操作 | 相同 |
