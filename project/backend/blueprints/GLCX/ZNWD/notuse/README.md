# 未使用的文件

此文件夹包含当前 V4/V5/V6 智能问答系统不再使用的旧版本文件。

## 文件列表

### 旧版本服务文件
- `services.py` - V1 版本的服务（旧版双路检索）
- `services_v2.py` - V2 版本的服务（支持思考过程输出）
- `services_v3.py` - V3 版本的服务（带记忆功能）
- `database_prompts.py` - V3 使用的提示词

### 文档和示例
- `example_template_config.py` - 模板配置使用示例
- `TEMPLATE_CONFIG_README.md` - 模板配置说明文档
- `说明.md` - 原始说明文档

## 为什么保留这些文件

这些文件被保留而不是删除，是因为：
1. 可能包含有用的历史代码参考
2. 旧版前端路由可能仍在引用（如 `/chat/stream`, `/chat/stream_v2`, `/chat/stream_v3`）
3. 便于后续需要时恢复

## 如何恢复

如果需要恢复某个文件，只需将其移回 `ZNWD` 根目录即可。

## 当前使用的文件

当前 V4/V5/V6 系统主要使用以下文件：
- `services_v4.py` - V4 版本服务
- `services_v5.py` - V5 版本服务
- `services_v6.py` - V6 版本服务
- `znwd_preflight.py` - V6 前置判别
- `znwd_v6_executor.py` - V6 执行器
- `znwd_langchain_v6.py` - V6 LangChain 集成
- `znwd_business_templates.py` - 业务模板定义
- `znwd_fast_route.py` - 快速路径
- `znwd_llm_intent_classifier.py` - LLM 意图分类
- `intent_classifier.py` - 规则意图分类
- `retrieval_meta_ops.py` - 元操作定义
- `business_templates.json` - 业务模板配置
- `znwd_template_config.py` - 模板配置加载器
