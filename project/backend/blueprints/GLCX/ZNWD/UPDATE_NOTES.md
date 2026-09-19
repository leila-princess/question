# ZNWD 智能问答系统更新说明

## 更新内容

### 1. LLM 意图分类器

**新增文件**: `znwd_llm_intent_classifier.py`

- 使用大语言模型进行意图分类，比规则更灵活准确
- 支持开关控制是否使用 LLM 分类

**环境变量**:
```bash
# 启用 LLM 意图分类（默认关闭）
ZNWD_USE_LLM_INTENT=1
```

**工作原理**:
- 当 `ZNWD_USE_LLM_INTENT=1` 且提供 `client` 时，使用 LLM 分类
- 否则回退到规则分类（`intent_classifier.py`）

**修改的文件**:
- `znwd_fast_route.py` - 导入改为从 `znwd_llm_intent_classifier`
- `znwd_preflight.py` - 导入并传递 `client` 和 `model` 参数
- `services_v5.py` - 导入并传递 `client` 和 `model` 参数

### 2. 业务模板配置

**新增文件**:
- `business_templates.json` - 业务模板配置文件
- `znwd_template_config.py` - 配置加载器

**新增模板**:
- `BIZ_INDICATOR_TO_METHOD` - 指标→方法查询
- `BIZ_METHOD_BY_RATE` - 按关联度排序查询方法

### 3. 文件整理

**移动到 `notuse/` 文件夹**:
- `services.py` (V1)
- `services_v2.py` (V2)
- `services_v3.py` (V3)
- `database_prompts.py`
- `example_template_config.py`
- `TEMPLATE_CONFIG_README.md`
- `说明.md`

## 如何使用 LLM 意图分类

### 方式 1：环境变量（推荐）

在 `.env` 文件或系统环境中设置：
```bash
ZNWD_USE_LLM_INTENT=1
```

### 方式 2：代码中控制

修改 `znwd_llm_intent_classifier.py` 中的默认值：
```python
_USE_LLM_INTENT = True  # 默认启用
```

## LLM 意图分类的优势

1. **更准确**：能理解更复杂的问法
2. **更灵活**：不需要维护大量正则规则
3. **可解释**：返回分类理由
4. **可降级**：LLM 失败时自动回退到规则

## 注意事项

1. LLM 分类会增加一次 LLM 调用，可能增加响应时间
2. 建议在生产环境充分测试后再启用
3. 可以通过日志查看分类结果：`[ZNWD][LLM-Intent]`
