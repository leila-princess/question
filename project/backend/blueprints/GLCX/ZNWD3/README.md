# ZNWD3 智能问答（新架构）

## 架构概述

ZNWD3 是 ZNWD1 的改进版，核心改进：
1. **LLM 只做轻量语义提取/确认**，不再做复杂路由决策
2. **程序负责验证和路由**，基于图谱 probe 结果
3. **零工具轮次**，probe 和历史读取全部程序直调
4. **图谱路径查找**，业务不命中时自动找实体间最短路径

## 两种模式

通过环境变量 `ZNWD3_MODE` 切换：

| 模式 | 说明 | LLM 任务 | 提示词 |
|------|------|----------|--------|
| `extract`（默认） | 语义提取 | 提取实体+意图 | ~300 token |
| `confirm` | 确认器 | 从 2-3 候选中选 | ~400 token |

## 数据流

```
用户问句
  → [程序] 打招呼检测 → direct_llm
  → [程序] 槽位粗提取（关键词扫描）
  → [程序] 实体 probe（Neo4j 验证）
  
  → 根据 ZNWD3_MODE 分流：
  
  extract 模式:
    → [LLM] 提取实体+意图（1 次调用）
    → [程序] 验证提取结果 + probe 匹配 → RoutePlan
  
  confirm 模式:
    → [程序] 预分类：槽位+probe → 2-3 候选业务
    → [LLM] 确认选择（1 次调用）
    → [程序] 组装 RoutePlan
  
  → [程序] plan_reconcile（guardrail + 兜底）
  → [程序] 业务不命中？→ graph_path_finder 找路径
  → [程序] expand → execute → 直出/合成
  → SSE 推送
```

## HTTP 接口

- `POST /api/znwd3/chat/stream` - 智能问答
- `POST /api/znwd3/source/table` - 来源表格
- `GET/DELETE /api/znwd3/memory` - 记忆管理

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `ZNWD3_MODE` | `extract` | 路由模式：extract 或 confirm |

## 文件结构

```
ZNWD3/
├── __init__.py                    # Flask 入口
├── pipeline.py                    # 主流水线
├── slot_extractor.py              # 程序槽位提取
├── entity_probe.py                # 程序直调 probe
├── semantic_extractor.py          # 方案 A：LLM 语义提取
├── pre_classifier.py              # 方案 B：程序预分类
├── confirmator.py                 # 方案 B：LLM 确认器
├── route_assembler.py             # 统一 RoutePlan 组装
├── graph_path_finder.py           # 图谱路径查找（1-2 跳）
├── prompts/
│   ├── extraction.py              # 语义提取提示词
│   └── confirmation.py            # 确认器提示词
└── (复用 ZNWD1 模块)
    ├── meta_ops.py
    ├── business_catalog*.py
    ├── business_expand.py
    ├── executor.py
    ├── formatters.py
    ├── plan_reconcile.py
    ├── playbook_resolver.py
    └── ...
```

## 与 ZNWD1 对比

| 指标 | ZNWD1 | ZNWD3 |
|------|-------|-------|
| LLM 调用次数 | 2-3 次 | 1 次 |
| 提示词 token | 3000-5000 | 300-400 |
| 工具轮次 | 有（probe/history） | 无 |
| 实体识别 | LLM 提取 | 图谱 probe 验证 |
| 路由决策 | LLM 决定 | 程序决定 |
| 预计延迟 | 10-15s | 4-7s |
