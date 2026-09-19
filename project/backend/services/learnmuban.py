from flask import Blueprint, request, jsonify
import uuid
import os
from extensions import client
import re
import json
import hashlib

createfa_bp = Blueprint("createfa", __name__)
SESSIONS = {}
TEMPLATE_CACHE = {}  # 缓存已分析的模板

def get_template_hash(template_content):
    """生成模板内容的哈希值用于缓存标识"""
    return hashlib.md5(template_content.encode()).hexdigest()

def extract_template_structure(template_content):
    """简化版模板结构提取，不依赖外部模型"""
    # 提取主要章节标题
    sections = re.findall(r'^(#+)\s+(.+)$', template_content, re.MULTILINE)
    
    structure = {
        "main_sections": [],
        "depth_levels": set(),
        "has_tables": "|" in template_content,  # 简单检测是否有表格
        "has_lists": re.search(r'\d+\.\s|\-\s|\*\s', template_content) is not None  # 检测是否有列表
    }
    
    for level, title in sections:
        level_depth = len(level)
        structure["depth_levels"].add(level_depth)
        structure["main_sections"].append({
            "title": title,
            "level": level_depth
        })
    
    return structure

def analyze_template(template_content):
    """使用LLM分析模板，提取关键结构和写作规范"""
    template_hash = get_template_hash(template_content)
    
    # 检查缓存
    if template_hash in TEMPLATE_CACHE:
        return TEMPLATE_CACHE[template_hash]
    
    # 先提取基本结构信息
    structure = extract_template_structure(template_content)
    
    # 设计分析提示词
    analysis_prompt = f"""
    你是一位专业的文档分析专家。请分析以下试验方案模板，提取其核心结构、写作规范和关键要素，
    但不要简单地复制模板内容。你的分析应该帮助其他LLM理解如何模仿这种风格和结构来创建新的试验方案。
    
    模板的基本结构信息：
    - 主要章节: {[section['title'] for section in structure['main_sections']]}
    - 标题层级: {sorted(structure['depth_levels'])}
    - 包含表格: {structure['has_tables']}
    - 包含列表: {structure['has_lists']}
    
    请提取以下信息：
    1. 文档的整体结构（章节组织方式）
    2. 各章节的典型内容和写作风格
    3. 使用的专业术语和表达方式
    4. 文档格式规范（编号体系、表格、图表等）
    5. 任何特定的写作要求或惯例
    
    模板内容（部分）：
    {template_content[:20000]}  # 限制长度
    
    请用清晰的结构化格式返回分析结果，以便其他LLM能够理解和应用。
    """
    
    try:
        # 调用LLM分析模板
        analysis_response = client.chat.completions.create(
            model="qwen-plus",
            messages=[
                {"role": "system", "content": "你是一位专业的文档分析专家，擅长提取文档结构和写作规范。"},
                {"role": "user", "content": analysis_prompt}
            ],
            temperature=0.1
        )
        
        analysis_result = analysis_response.choices[0].message.content
        
        # 缓存结果
        TEMPLATE_CACHE[template_hash] = analysis_result
        return analysis_result
        
    except Exception as e:
        print(f"模板分析失败: {str(e)}")
        # 失败时返回简化版分析
        structure_desc = f"模板包含以下章节: {[section['title'] for section in structure['main_sections']]}"
        return f"模板分析摘要: {structure_desc}。这是一个试验方案模板，请参考其结构但根据实际内容创作。"

def read_template_file():
    """读取模板文件并进行分析"""
    template_path = 'public/Syfatext/example1.md'
    try:
        with open(template_path, 'r', encoding='utf-8') as file:
            template_content = file.read()
        template_analyze = analyze_template(template_content)
        # 定义保存路径（当前目录下的template_analysis.txt）
        analysis_file_path = 'public/Syfatext/template_analysis.txt'

        # 以文本模式写入字符串内容
        with open(analysis_file_path, 'w', encoding='utf-8') as f:
            f.write(template_analyze)  # 直接写入字符串
        
        print(f"模板分析结果已保存至: {analysis_file_path}")
        # 分析模板并返回分析结果
        return template_analyze
        
    except FileNotFoundError:
        base_template = "# 试验方案模板\n\n## 1. 试验目的\n\n## 2. 试验原理\n\n## 3. 试验设备与材料\n\n## 4. 试验步骤\n\n## 5. 数据记录与处理\n\n## 6. 安全注意事项\n\n## 7. 参考文献"
        return analyze_template(base_template)
    except Exception as e:
        return f"读取模板文件时出错: {str(e)}"
