# ZNWD 业务模板配置说明

## 概述

业务模板配置文件 `business_templates.json` 允许你在不修改代码的情况下，动态管理智能问答的业务逻辑。

## 文件位置

```
backend/blueprints/GLCX/ZNWD/business_templates.json
```

## 配置文件结构

```json
{
  "version": "1.0",
  "description": "ZNWD 业务模板配置",
  "templates": [
    {
      "template_code": "模板代码",
      "template_name": "模板名称",
      "description": "模板描述",
      "intent_type": "意图类型",
      "answer_mode": "回答模式",
      "question_patterns": ["问法正则"],
      "slots": {
        "槽位名": {
          "type": "类型",
          "values": ["枚举值"],
          "default": "默认值"
        }
      },
      "steps": [
        {
          "step_id": "s1",
          "op_code": "元操作代码",
          "params": {
            "参数": "${槽位变量}"
          },
          "purpose": "步骤描述（支持模板变量）"
        }
      ]
    }
  ]
}
```

## 字段说明

### 模板级别字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| template_code | string | 是 | 模板唯一标识，如 `BIZ_REL_ONE_HOP` |
| template_name | string | 是 | 模板中文名称 |
| description | string | 是 | 模板用途描述 |
| intent_type | string | 是 | 意图类型：`ENUMERATION`、`RELATION_QUERY`、`STANDARD_CHAPTER` |
| answer_mode | string | 是 | 回答模式：`database_direct`（数据库直出）、`llm_synthesize`（LLM 整合） |
| question_patterns | array | 是 | 问法匹配正则数组，支持 `{focus}` 占位符 |
| slots | object | 是 | 槽位定义（见下方） |
| steps | array | 是 | 执行步骤数组（见下方） |

### 槽位字段（slots）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| type | string | 是 | 槽位类型：`enum`（枚举）、`string`（字符串） |
| values | array | 否 | 枚举类型的可选值 |
| default | string | 否 | 默认值 |
| description | string | 否 | 槽位描述 |

### 步骤字段（steps）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| step_id | string | 是 | 步骤标识，如 `s1`、`s2` |
| op_code | string | 是 | 元操作代码，见 `retrieval_meta_ops.py` |
| params | object | 是 | 操作参数，支持 `${变量名}` 模板替换 |
| purpose | string | 是 | 步骤描述（中文），支持模板变量 |

## 可用的元操作（op_code）

| op_code | 类型 | 说明 |
|---------|------|------|
| neo_node_query | neo4j | 单标签节点模糊查询 |
| neo_list_nodes | neo4j | 按节点标签列举 |
| neo_relation_query | neo4j | 单跳关系查询 |
| neo_property_filter | neo4j | 属性过滤 |
| chroma_vector_search | chroma | 向量召回文档 |
| chroma_score_filter | chroma | 按相似度过滤 |
| chroma_rerank | chroma | 重排序 |
| chroma_section_expand | chroma | 按章节前缀扩展 |

## 模板变量替换

在 `params` 和 `purpose` 中可以使用以下变量：

- `${slot_name}`: 从 slots 中取值
- `${retrieval_question}`: 替换为完整的检索问题

示例：
```json
{
  "params": {
    "from_type": "${from_type}",
    "value": "${focus}"
  },
  "purpose": "根据「${focus}」查找 ${from_type} 关联的 ${to_type}"
}
```

## 如何新增业务模板

### 步骤 1：编辑配置文件

打开 `business_templates.json`，在 `templates` 数组中添加新模板：

```json
{
  "template_code": "BIZ_REL_THREE_HOP",
  "template_name": "三跳关系查询",
  "description": "查询 A→B→C→D 的三跳关系",
  "intent_type": "RELATION_QUERY",
  "answer_mode": "database_direct",
  "question_patterns": [
    ".*{focus}.*项目.*指标.*方法.*"
  ],
  "slots": {
    "from_type": {
      "type": "enum",
      "values": ["Equipment", "Environment"]
    },
    "focus": {
      "type": "string"
    }
  },
  "steps": [
    {
      "step_id": "s1",
      "op_code": "neo_relation_query",
      "params": {
        "from_type": "${from_type}",
        "rel_type": "CON",
        "to_type": "TestProject",
        "match_mode": "fuzzy",
        "value": "${focus}"
      },
      "purpose": "根据「${focus}」查找 ${from_type} 关联的测试项目"
    },
    {
      "step_id": "s2",
      "op_code": "neo_relation_query",
      "params": {
        "from_type": "TestProject",
        "rel_type": "CON",
        "to_type": "Indicator",
        "match_mode": "in",
        "from_values_ref": {"step_id": "s1", "target": "m", "field": "Id"}
      },
      "purpose": "查找这些测试项目关联的指标"
    },
    {
      "step_id": "s3",
      "op_code": "neo_relation_query",
      "params": {
        "from_type": "Indicator",
        "rel_type": "CON",
        "to_type": "TestMethod",
        "match_mode": "in",
        "from_values_ref": {"step_id": "s2", "target": "m", "field": "Id"}
      },
      "purpose": "查找这些指标关联的测试方法"
    }
  ]
}
```

### 步骤 2：重新加载配置

在代码中调用：
```python
from .znwd_template_config import reload_template_config
reload_template_config()
```

或重启服务。

## 如何修改现有模板

1. 找到 `business_templates.json` 中对应的模板
2. 修改 `question_patterns`、`steps` 等字段
3. 重新加载配置

## 如何禁用模板

将模板的 `question_patterns` 设置为空数组：
```json
"question_patterns": []
```

或直接删除该模板配置。

## 注意事项

1. **模板代码唯一性**：每个 `template_code` 必须唯一
2. **正则表达式**：`question_patterns` 中的正则会被自动添加 `.*` 匹配 `{focus}`
3. **参数校验**：`params` 中的字段必须符合 `retrieval_meta_ops.py` 中定义的 `params_schema`
4. **热更新**：修改配置文件后需调用 `reload_template_config()` 或重启服务

## 示例：常见业务模板

### 示例 1：查询某设备的所有测试项目

```json
{
  "template_code": "BIZ_EQUIP_PROJECTS",
  "template_name": "设备测试项目查询",
  "intent_type": "RELATION_QUERY",
  "answer_mode": "database_direct",
  "question_patterns": [
    ".*{focus}.*有哪些.*项目.*"
  ],
  "slots": {
    "focus": {"type": "string"}
  },
  "steps": [
    {
      "step_id": "s1",
      "op_code": "neo_relation_query",
      "params": {
        "from_type": "Equipment",
        "rel_type": "CON",
        "to_type": "TestProject",
        "match_mode": "fuzzy",
        "value": "${focus}"
      },
      "purpose": "查找「${focus}」关联的测试项目"
    }
  ]
}
```

### 示例 2：查询某环境的所有测试方法（两跳）

```json
{
  "template_code": "BIZ_ENV_METHODS",
  "template_name": "环境测试方法查询",
  "intent_type": "RELATION_QUERY",
  "answer_mode": "database_direct",
  "question_patterns": [
    ".*{focus}.*的方法.*"
  ],
  "slots": {
    "focus": {"type": "string"}
  },
  "steps": [
    {
      "step_id": "s1",
      "op_code": "neo_relation_query",
      "params": {
        "from_type": "Environment",
        "rel_type": "CON",
        "to_type": "TestProject",
        "match_mode": "fuzzy",
        "value": "${focus}"
      },
      "purpose": "根据「${focus}」查找试验环境关联的测试项目"
    },
    {
      "step_id": "s2",
      "op_code": "neo_relation_query",
      "params": {
        "from_type": "TestProject",
        "rel_type": "CON",
        "to_type": "TestMethod",
        "match_mode": "in",
        "from_values_ref": {"step_id": "s1", "target": "m", "field": "Id"}
      },
      "purpose": "查找这些测试项目关联的测试方法"
    }
  ]
}
```

## 与代码的关系

- **配置文件**：定义业务逻辑（模板、槽位、步骤）
- **retrieval_meta_ops.py**：定义底层能力（元操作、节点类型、关系）
- **znwd_template_config.py**：配置加载器（读取、匹配、生成 program）
- **znwd_business_templates.py**：现有代码（可逐步迁移到配置文件）

新增业务时，只需修改配置文件，无需修改代码。
