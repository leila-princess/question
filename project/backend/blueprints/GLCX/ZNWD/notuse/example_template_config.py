"""
ZNWD 业务模板配置使用示例

展示如何使用配置文件管理业务模板，无需修改代码。
"""

from .znwd_template_config import get_template_config, reload_template_config


def example_usage():
    """使用示例"""
    # 1. 获取配置实例
    config = get_template_config()

    # 2. 查看所有已加载的模板
    print("已加载的模板:", config.get_all_templates().keys())

    # 3. 匹配问法
    question = "无人机有哪些试验项目？"
    template = config.match_template(question, "RELATION_QUERY")
    if template:
        print(f"匹配到模板: {template['template_code']}")

    # 4. 生成 program
    slots = {
        "from_type": "Equipment",
        "to_type": "TestProject",
        "focus": "无人机"
    }
    program = config.build_program("BIZ_REL_ONE_HOP", slots)
    print("生成的 program:", program)

    # 5. 热更新配置（修改配置文件后调用）
    reload_template_config()


def example_add_new_template():
    """
    示例：如何新增一个"三跳关系查询"模板
    
    只需在 business_templates.json 中添加以下配置：
    
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
            "from_field": "Name",
            "match_mode": "fuzzy",
            "limit": 120,
            "where_side": "from",
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
            "from_field": "Id",
            "match_mode": "in",
            "limit": 200,
            "from_values_ref": {
              "step_id": "s1",
              "target": "m",
              "field": "Id"
            }
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
            "from_field": "Id",
            "match_mode": "in",
            "limit": 200,
            "from_values_ref": {
              "step_id": "s2",
              "target": "m",
              "field": "Id"
            }
          },
          "purpose": "查找这些指标关联的测试方法"
        }
      ]
    }
    
    然后调用 reload_template_config() 即可生效，无需修改代码。
    """
    pass
