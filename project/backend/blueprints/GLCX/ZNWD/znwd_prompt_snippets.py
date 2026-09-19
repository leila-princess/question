"""提示词片段（无业务模块依赖，供 prompts_v5/v6 引用）。"""

LLM_THINKING_LANGUAGE_RULES = """
## 思考过程（`<THINK>` / 检索规划，必须极简以省 token）
- **只写推理意图**：打算查什么、为何查（**最多 1～2 句**，每句宜短）。
- **禁止**在思考区写：检索结果、命中条数、返回列表、工具返回值摘要、「未命中/检索失败/无数据/retrieval_gap」等结论与原因；这些只写在 `<CONTENT>` 正文。
- 用中文业务词：清单库、文档库、设备、试验环境、测试项目、指标、测试方法；**禁止**英文标签、CON/HAS、op_code、BIZ_、TOOL_ 名、JSON 字段名、箭头路径写法。
"""

__all__ = ("LLM_THINKING_LANGUAGE_RULES",)
