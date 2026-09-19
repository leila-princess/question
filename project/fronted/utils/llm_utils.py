import json
import re
import uuid
import datetime
import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

def llm_chat(client, model, messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
    """调用LLM"""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
        )
        data = json.loads(resp.model_dump_json())
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.exception("LLM 调用失败: %s", e)
        return f"【占位回答】LLM 调用失败：{e}"

def extract_document_structure_with_llm(client, model, file_bytes: bytes) -> Dict[str, Any]:
    """使用大模型解析Word文档结构"""
    import io
    from docx import Document
    
    try:
        # 1. 首先用传统方法提取基础内容
        doc = Document(io.BytesIO(file_bytes))
        full_text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        
        # 2. 构建大模型解析提示词
        prompt = f"""
        请分析以下Word文档内容，提取以下结构化信息：
        1. 文档标题（最顶部的正式名称）
        2. 章节结构（识别各级标题和对应内容）
        3. 全文内容（保留原始格式）

        要求：
        - 标题识别：文档开头的正式名称作为主标题
        - 章节识别：带编号的标题（如"1.1 引言"）作为章节
        - 内容归属：将内容归属到最近的上级标题下
        - 输出格式：严格的JSON格式

        文档内容：
        {full_text[:20000]}...（总长度：{len(full_text)}字符）
        """

        # 3. 调用大模型解析
        response = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": prompt
            }],
            temperature=0.3
        )
        
        # 4. 解析大模型返回结果
        result = json.loads(response.choices[0].message.content)
        
        # 5. 补充原始全文内容
        result["full_text"] = full_text
        
        return {
            "title": result.get("title", "未命名文档"),
            "headings": result.get("headings", []),
            "sections": result.get("sections", []),
            "full_text": full_text
        }

    except Exception as e:
        logger.error(f"大模型解析失败: {str(e)}")
        # 降级处理：使用传统方法
        from .document_utils import extract_document_structure
        return extract_document_structure(file_bytes)

def build_chapter_parse_prompt(full_content, domain):
    """构建解析章节目录的提示词"""
    return f"""
    请严格按以下要求解析{domain}文档结构，提取文档元数据及章节目录：

    # 元数据提取要求
    1. 文档标题：识别文档最顶部的正式名称（如"XX设备环境试验规范"）
    2. 文档信息：提取文档属性（如标准号"GB/T 2423.1-2020"、发布日期、起草单位等）
    3. 适用范围：文档开头描述的适用对象或范围

    # 章节解析要求
    1. 层级识别：
       - 一级章节（如"1 范围"、"2 规范性引用文件"）
       - 二级章节（如"3.1 试验条件"、"3.2 试验设备"）
       - 三级章节（如"4.1.1 温度要求"、"4.1.2 湿度要求"）
    2. 内容要求：
       - 每个章节必须包含完整文本内容
       - 保留原始格式中的换行和列表结构
    3. 特殊处理：
       - 无编号的章节按逻辑关系归类
       - 表格/图表需标注[TABLE]/[FIGURE]并保留标题

    # 输出格式（严格JSON）
    {{
      "metadata": {{
        "title": "文档完整标题",
        "standard_code": "标准编号（可选）",
        "publish_date": "发布日期（可选）",
        "scope": "适用范围说明"
      }},
      "toc": ["章节1标题", "章节2标题", ...],
      "chapters": [
        {{
          "title": "章节标题",
          "level": 1,  // 1|2|3表示层级
          "content": "完整章节内容",
          "tables": ["表1标题", ...],  // 可选
          "figures": ["图1标题", ...]  // 可选
        }},
        ...
      ]
    }}

    # 待解析文档内容
    {full_content}
    """

def build_start_question_prompt(metadata, domain):
    """构建起始问题提示词"""
    return f"""
    # 指令
    请根据以下信息生成1-2个确认文档需求的问题，要求：
    - 每个问题必须以"Q:"开头
    - 问题必须与{domain}领域相关
    - 只生成确认性提问，可以包含建议的回答
    
    # 输出格式示例
    Q: 需要测试的无人机型号是什么？
    Q: 文档编号是否需要遵循特定命名规范？
    
    # 上下文信息
    领域: {domain}
    文档标题: {metadata.get('title', '无标题')}
    标准编号: {metadata.get('standard_code', '无')}
    """

def build_chapter_question_prompt(chapter, metadata, domain, history):
    """构建章节问题提示词"""
    prompt = f"""
    根据以下信息生成问题：
    领域: {domain}
    文档标题: {metadata if metadata else '无标题'}
    当前章节: {chapter.get('title', '无章节')}
    
    历史上下文:
    {history if history else '无历史信息'}
    
    章节完整内容:
    {chapter.get('content', '无内容')}
    
    我们要根据参考文档生成用户需要的实验方案文档。请生成3-5个针对该章节的技术问题，要求：
    - 每个问题必须以"Q:"开头
    - 问题必须与{domain}领域相关
    - 只生成确认性提问，可以包含建议的回答
    
    # 输出格式示例
    Q: 需要测试的无人机型号是什么？
    Q: 文档编号是否需要遵循特定命名规范？

    同时判断是否是第一次询问，如果不是请根据历史上下文生成已有章节的试验方案
    要求：
    1. 按章节逻辑组织报告，保持连贯性
    2. 整合问答内容，突出核心结论
    3. 格式规范，使用Markdown标题层级区分章节

    # 输出格式（严格JSON）
    {{
      "question": 问题,
      "preview": 已有章节的试验方案,      
    }}

    """
    return prompt

def build_final_report_prompt(title, metadata, chapters, answers, domain):
    """构建最终报告提示词"""
    chapter_answers = []
    for i, (chapter, ans) in enumerate(zip(chapters, answers['chapter_0']['answers'].values())):
        chapter_answers.append(f"章节{i+1}：{chapter['title']}\n内容：{chapter['content'][:200]}...\n答案：{ans}")
    
    return f"""
    基于以下{domain}的章节问答，生成word文档：
    {chr(10).join(chapter_answers)}
    
    要求：
    1. 按章节逻辑组织报告，保持连贯性
    2. 整合问答内容，突出核心结论
    3. 格式规范，使用Markdown标题层级区分章节
    """

def parse_model_response(text):
    """解析模型返回的结构化内容"""
    # 尝试提取 Markdown 代码块中的 JSON
    match = re.search(r'```json\n?(.*?)\n?```', text, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        json_str = text.strip()
    try:
        data = json.loads(json_str)
        question = data.get("question", "")
        preview = data.get("preview", "")
    except Exception:
        # 降级处理：如果不是标准 JSON，直接返回原始内容
        question = ""
        preview = ""
    return {
        "question": question,
        "preview": preview
    }

def parse_questions(text):
    """解析标准化格式的问题文本"""
    questions = []
    # 处理不同类型的输入
    if isinstance(text, list):
        # 如果是列表，处理每个元素
        processed_items = []
        for item in text:
            if isinstance(item, str):
                processed_item = item.replace('\r\n', '\n').strip()
                processed_items.append(processed_item)
            else:
                processed_items.append(str(item))
        return processed_items
    elif isinstance(text, str):
        # 如果是字符串，按原逻辑处理
        # 标准化处理换行符
        text = text.replace('\r\n', '\n').strip()   
        # 按行提取以"Q:"开头的问题
        for line in text.split('\n'):
            line = line.strip()
            if line.startswith('Q:'):
                question_text = line[2:].strip()
                if question_text:  # 非空问题
                    questions.append({
                        "id": f"q{len(questions)+1}",
                        "text": question_text,
                        "type": "text"
                    })
        
        # 保底：尝试分割问号
        if not questions:
            questions = [
                {
                    "id": f"q{i+1}",
                    "text": q.strip(),
                    "type": "text"
                }
                for i, q in enumerate(re.split(r'(?<=[?？])\s+', text))
                if q.strip() and q.strip()[-1] in ('?', '？')
            ][:5]  # 最多取2个
        
        return questions
    return questions[:5]  # 严格限制数量