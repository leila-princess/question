# 纯 LLM 模式（因果骨架 + 规划器补全）

与 `routes.py` 配置模式**解耦**，入口：

- HTTP SSE：`POST /api/fzsj/jyllm/pure-llm/run`
- 命令行 Demo：`python -m blueprints.FZSJ.JYLLM.pure_llm.demo`（在 `backend` 目录）

## 流程

1. **LLM Agent** 多轮调用 Neo4j / Chroma 工具（复用 `znwd_v6_tools`，**禁止 mock**）  
2. LLM 生成含分支的 **STRIPS PDDL**（domain + problem）  
3. **pyperplan** 优先求解，失败则 **Python STRIPS** 正向搜索  
4. 将图谱 TestMethod 注入 domain 后 **重规划**  
5. HTML 可视化：领域结构图、规划空间图（含未选分支）、工具调用轨迹  
6. LLM **流式**撰写 Markdown 方案（SSE：`scheme_delta` 等）

## SSE 事件（流式）

| 事件 | 说明 |
|------|------|
| `tool_round` / `tool_result` | 工具调用实时推送 |
| `retrieve_delta` | 检索 Agent 文本片段 |
| `pddl_delta` | PDDL 生成 token 流 |
| `plan_step` | 规划解逐步输出 |
| `scheme_delta` | 方案正文 token 流（右侧面板实时预览） |
| `complete` | 结束，含 chapters / figures |

## 依赖

```bash
pip install -r blueprints/FZSJ/JYLLM/pure_llm/requirements.txt
```

**默认使用 HTML/SVG 过程图**（无 matplotlib 依赖，避免 numpy 1.x/2.x 冲突）。生成 `process_dashboard.html` 及分图 HTML，浏览器打开任务目录或通过接口 `/pure-llm/asset/<task_id>/process_dashboard.html` 查看。

若需要 PNG，在运行前设置：

```bash
set JYLLM_PURE_LLM_USE_MATPLOTLIB=1
pip install "numpy<2" matplotlib
```

## 前端

`JYLLM` 页顶部分段控件切换「配置模式 / 纯LLM模式」。不需要时可注释掉 `Segmented` 相关代码，配置模式逻辑不受影响。
