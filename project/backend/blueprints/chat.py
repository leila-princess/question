from flask import Blueprint, request, jsonify, current_app
import json
import uuid
import logging

logger = logging.getLogger(__name__)

chat_bp = Blueprint('chat', __name__, url_prefix='/api/chat')

# 标准内容存储（实际应用中可以从数据库或文件加载）
STANDARD_CONTENTS = {
    "low_temp": """
    《GB/T 38924.2-2020 民用轻小型无人机系统环境试验方法 第2部分：低温试验》
    1. 试验范围：起飞重量0.25kg～150kg的民用轻小型无人机系统
    2. 试验分类：低温贮存试验和低温工作试验
    3. 低温贮存试验条件：
       - 试验温度：根据使用地域选择（寒带-45℃、温带-30℃、热带0℃）
       - 持续时间：达到温度稳定后再保持24小时
       - 温度变化率：≤3℃/min
    4. 低温工作试验条件：
       - 飞行器试验温度：
         * 1000m以下：寒带-30℃、温带-10℃、热带0℃
         * 1000m～3000m：寒带-30℃、温带-20℃、热带0℃
         * 3000m～5000m：寒带-40℃、温带-30℃、热带-10℃
         * 5000m以上：寒带-45℃、温带-35℃、热带-20℃
       - 地面站试验温度：
         * 舱内：寒带-10℃、温带0℃、热带0℃
         * 舱外：寒带-30℃、温带-20℃、热带0℃
       - 持续时间：达到温度稳定后继续保持2小时，然后进行功能测试
    5. 试验过程：试验准备→预处理→试验前检测→试验步骤→恢复→试验后检测
    """,
    
    "high_temp": """
    《GB/T 38924.3-2020 民用轻小型无人机系统环境试验方法 第3部分：高温试验》
    1. 试验分类：高温贮存试验和高温工作试验
    2. 高温贮存试验条件：
       - 试验温度：推荐70℃
       - 持续时间：达到温度稳定后再保持3小时
       - 温度变化率：≤3℃/min
    3. 高温工作试验条件：
       - 试验温度：
         * 飞行器和地面站舱外部分：55℃
         * 地面站舱内部分：40℃
       - 持续时间：达到温度稳定后继续保持2小时，之后工作至少2小时
    4. 试验过程：与低温试验相似，高温工作试验需在高温条件下实际工作
    5. 通用要求：设备技术状态相符、仪器校准、试验箱容积比≥5:1
    """
}

@chat_bp.route('/drone_test', methods=['POST'])
def drone_environment_test():
    """专门处理无人机环境试验问题的接口"""
    try:
        data = request.get_json()
        question = data.get('message', '').strip()
        
        if not question:
            return jsonify({
                "success": False,
                "error": "问题不能为空",
                "code": 400
            }), 400
        
        # 构建完美提示词
        perfect_prompt = build_perfect_prompt(question)
        
        # 获取回答
        response = get_drone_test_response(perfect_prompt)
        
        return jsonify({
            "success": True,
            "question": question,
            "response": response,
            "promptLength": len(perfect_prompt),
            "responseLength": len(response)
        })
        
    except Exception as e:
        logger.error(f"无人机试验接口错误: {str(e)}")
        return jsonify({
            "success": False,
            "error": f"处理失败: {str(e)}",
            "code": 500
        }), 500

def build_perfect_prompt(question):
    """构建完美的提示词"""
    
    # 根据问题类型选择不同的提示词模板
    if "高温" in question and "低温" in question:
        prompt_template = """你是一位无人机环境试验标准专家，请基于以下提供的GB/T 38924系列标准内容，详细回答关于无人机高温试验和低温试验方法的问题。

【提供的标准内容】
{low_temp_content}

{high_temp_content}

【用户问题】
{question}

【回答要求】
请按照以下结构组织回答：
1. 概述：简要说明无人机环境试验的目的和两类试验的基本区别
2. 低温试验方法：
   - 分类说明（贮存试验 vs 工作试验）
   - 详细试验条件（温度、时间、变化率）
   - 试验步骤流程
   - 关键参数表格
3. 高温试验方法：
   - 分类说明（贮存试验 vs 工作试验）
   - 详细试验条件（温度、时间、变化率）
   - 试验步骤流程
   - 关键参数表格
4. 对比分析：用表格对比高低温试验的主要差异
5. 试验注意事项：设备要求、中断处理、结果评定等共同要点
6. 应用建议：针对不同使用场景的试验选择建议

请确保回答：
- 完全基于提供的标准内容，不添加外部知识
- 引用具体的标准条款和参数
- 使用专业术语，保持技术准确性
- 结构清晰，便于理解和应用
"""
    elif "低温" in question:
        prompt_template = """你是一位无人机环境试验标准专家，请基于以下提供的GB/T 38924.2标准内容，详细回答关于无人机低温试验方法的问题。

【标准内容】
{low_temp_content}

【用户问题】
{question}

【回答要求】
请详细说明低温试验的分类、条件、步骤和注意事项。
"""
    elif "高温" in question:
        prompt_template = """你是一位无人机环境试验标准专家，请基于以下提供的GB/T 38924.3标准内容，详细回答关于无人机高温试验方法的问题。

【标准内容】
{high_temp_content}

【用户问题】
{question}

【回答要求】
请详细说明高温试验的分类、条件、步骤和注意事项。
"""
    else:
        # 通用无人机试验问题
        prompt_template = """你是一位无人机环境试验标准专家，请基于以下提供的GB/T 38924系列标准内容，回答关于无人机环境试验的问题。

【标准内容】
{low_temp_content}

{high_temp_content}

【用户问题】
{question}

请基于标准内容给出专业、准确的回答。
"""
    
    # 填充模板
    prompt = prompt_template.format(
        question=question,
        low_temp_content=STANDARD_CONTENTS["low_temp"],
        high_temp_content=STANDARD_CONTENTS["high_temp"]
    )
    
    return prompt

def get_drone_test_response(prompt):
    """调用大模型获取回答"""
    try:
        client = current_app.config['OPENAI_CLIENT']
        model = current_app.config.get('OPENAI_MODEL', 'qwen-plus')
        
        # 调用模型
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一位专业的无人机标准专家，严格按照提供的标准内容回答问题。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,  # 较低的温度，确保回答更准确
            max_tokens=3000
        )
        
        response = completion.choices[0].message.content
        
        # 记录日志
        logger.info(f"无人机试验问题处理完成，提示词长度: {len(prompt)}, 回答长度: {len(response)}")
        
        return response
        
    except Exception as e:
        logger.error(f"获取模型回答失败: {str(e)}")
        return f"抱歉，处理请求时出现错误: {str(e)}"

@chat_bp.route('/standard_query', methods=['POST'])
def query_standard():
    """查询特定标准内容的接口"""
    try:
        data = request.get_json()
        standard_type = data.get('type', '').strip()  # low_temp, high_temp, all
        
        if standard_type == 'low_temp':
            content = STANDARD_CONTENTS["low_temp"]
            standard_name = "GB/T 38924.2-2020 低温试验"
        elif standard_type == 'high_temp':
            content = STANDARD_CONTENTS["high_temp"]
            standard_name = "GB/T 38924.3-2020 高温试验"
        elif standard_type == 'all':
            content = f"{STANDARD_CONTENTS['low_temp']}\n\n{STANDARD_CONTENTS['high_temp']}"
            standard_name = "GB/T 38924.2 & 3 高低温试验"
        else:
            return jsonify({
                "success": False,
                "error": "无效的标准类型",
                "code": 400
            }), 400
        
        return jsonify({
            "success": True,
            "standardName": standard_name,
            "content": content,
            "contentLength": len(content)
        })
        
    except Exception as e:
        logger.error(f"查询标准失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@chat_bp.route('/test_demo', methods=['GET'])
def test_demo():
    """测试接口，返回一个示例回答"""
    demo_question = "关于无人机高温试验、低温试验的试验方法有哪些？"
    demo_prompt = build_perfect_prompt(demo_question)
    
    return jsonify({
        "success": True,
        "demoQuestion": demo_question,
        "promptPreview": demo_prompt[:500] + "..." if len(demo_prompt) > 500 else demo_prompt,
        "promptLength": len(demo_prompt),
        "endpoint": "POST /api/chat/drone_test 用于实际查询"
    })