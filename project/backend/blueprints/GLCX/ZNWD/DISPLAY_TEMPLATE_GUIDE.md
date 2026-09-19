# 业务模板显示文本配置说明

## 概述

为了让前端展示用户可读的思考过程，每个业务模板和步骤都需要配置语义化的显示文本。

## 配置位置

`backend/blueprints/GLCX/ZNWD/business_templates.json`

## 配置结构

### 1. 步骤级别：`display_text`

每个步骤可以配置 `display_text` 字段，用于前端展示该步骤正在执行的内容。

**示例**：
```json
{
  "step_id": "s1",
  "op_code": "neo_relation_query",
  "params": {...},
  "purpose": "根据「无人机」查找 Equipment 关联的 TestProject",
  "display_text": "正在根据「无人机」查找设备关联的测试项目..."
}
```

**说明**：
- `purpose`：内部使用，记录步骤的技术性描述
- `display_text`：前端展示，用户可读的语义化描述

**支持的变量**：
- `${focus}`：焦点实体（如"无人机"）
- `${from_type_zh}`：起点节点类型中文（如"设备"）
- `${to_type_zh}`：终点节点类型中文（如"测试项目"）
- `${mid_type_zh}`：中间节点类型中文

### 2. 模板级别：`display_template`

模板可以配置 `display_template` 字段，定义整体的检索规划展示文本。

**示例**：
```json
{
  "template_code": "BIZ_REL_TWO_HOP",
  "display_template": {
    "planning": "【检索规划】\n1. 结合对话历史对用户意图的理解：${intent_summary}\n2. 拟在图谱中了解的内容：首先根据「${focus}」查找${from_type_zh}关联的${mid_type_zh}，然后查找这些${mid_type_zh}关联的${to_type_zh}\n3. 检索顺序：第1步：根据「${focus}」查找${from_type_zh}关联的${mid_type_zh} → 第2步：查找这些${mid_type_zh}关联的${to_type_zh}\n4. 以下开始按规划执行程序化检索。",
    "step_prefix": "正在执行第${step_num}步检索："
  }
}
```

**字段说明**：
- `planning`：检索规划的完整展示文本
- `step_prefix`：每个步骤的前缀文本

**支持的变量**：
- `${intent_summary}`：意图总结
- `${focus}`：焦点实体
- `${from_type_zh}`、`${to_type_zh}`、`${mid_type_zh}`：节点类型中文
- `${step_num}`：当前步骤编号

## 流式输出流程

后端按以下步骤流式输出到前端：

```
1. 发送检索规划（一次性）
   ↓ event: thinking
   【检索规划】
   1. 结合对话历史对用户意图的理解：...
   2. 拟在图谱中了解的内容：...
   3. 检索顺序：...
   4. 以下开始按规划执行程序化检索。

2. 执行第1步
   ↓ event: step (loading)
   第1步：正在根据「无人机」查找设备关联的测试项目...
   
   [执行检索...]
   
   ↓ event: step (done)
   第1步完成：找到 5 条图谱数据，0 条文档数据

3. 执行第2步
   ↓ event: step (loading)
   第2步：正在查找这些测试项目关联的测试方法...
   
   [执行检索...]
   
   ↓ event: step (done)
   第2步完成：找到 3 条图谱数据，0 条文档数据

4. 检索完成
   ↓ event: step (done)
   程序化检索完成：Neo=8，Chroma=0
```

## 新增模板时的配置步骤

1. **定义步骤的 `display_text`**：
   - 使用用户可读的中文描述
   - 包含焦点实体和节点类型中文
   - 以"正在..."开头，表示执行中

2. **定义模板的 `display_template`**：
   - `planning`：完整的检索规划文本
   - `step_prefix`：步骤前缀（可选）

3. **测试验证**：
   - 启动服务
   - 发送测试问题
   - 检查前端展示的思考过程是否语义化

## 示例：新增"指标→方法"模板

```json
{
  "template_code": "BIZ_INDICATOR_TO_METHOD",
  "template_name": "指标→方法查询",
  "intent_type": "RELATION_QUERY",
  "answer_mode": "llm_synthesize",
  "question_patterns": [".*{focus}.*指标.*方法.*"],
  "slots": {
    "focus": {"type": "string"}
  },
  "steps": [
    {
      "step_id": "s1",
      "op_code": "neo_node_query",
      "params": {
        "node_type": "Indicator",
        "field": "Name",
        "value": "${focus}",
        "limit": 80
      },
      "purpose": "定位考核指标「${focus}」",
      "display_text": "正在定位考核指标「${focus}」..."
    },
    {
      "step_id": "s2",
      "op_code": "neo_relation_query",
      "params": {
        "from_type": "Indicator",
        "rel_type": "CON",
        "to_type": "TestMethod",
        "from_field": "Id",
        "match_mode": "in",
        "limit": 200,
        "from_values_ref": {"step_id": "s1", "target": "n", "field": "Id"}
      },
      "purpose": "查找「${focus}」指标对应的测试方法",
      "display_text": "正在查找「${focus}」指标对应的测试方法..."
    }
  ],
  "display_template": {
    "planning": "【检索规划】\n1. 结合对话历史对用户意图的理解：查询指标「${focus}」关联的测试方法\n2. 拟在图谱中了解的内容：首先定位考核指标「${focus}」，然后查找该指标对应的测试方法\n3. 检索顺序：第1步：定位考核指标 → 第2步：查找关联的测试方法\n4. 以下开始按规划执行程序化检索。",
    "step_prefix": "正在执行第${step_num}步检索："
  }
}
```

## 注意事项

1. **语义化优先**：`display_text` 应该使用用户能理解的业务语言，避免技术术语
2. **包含关键信息**：应包含焦点实体、节点类型等关键信息
3. **保持一致性**：同一模板的多个步骤，描述风格应保持一致
4. **变量替换**：确保使用的变量在 `slots` 中已定义
