from flask import Blueprint, request, jsonify, current_app, Response, stream_with_context
import datetime
import os
import json
import re
import logging
import time
import threading
import hashlib
from utils.llm_utils import (
    llm_chat,
    build_chapter_parse_prompt,
    build_start_question_prompt,
    build_chapter_question_prompt,
    build_final_report_prompt,
    parse_model_response,
    parse_questions
)
from utils.document_utils import save_word_file
import shelve
from pathlib import Path

experiment_bp = Blueprint('experiment', __name__, url_prefix='/api/experiment')

# 存储章节生成器实例
section_generators = {}
logger = logging.getLogger(__name__)

# 高温试验方案配置 - 修正章节编号
HIGH_TEMP_CONFIG = {
    "domain": "无人机领域",
    "product_type": "military",
    "test_purpose": "qualification",
    "test_type": ["temperature"],
    "standard_system": "国标",
    "device_name": "Matrice 350 RTK型无人机",
    "doc_id": "WRJ-HJSY-2025-001",
    "doc_title": "Matrice 350 RTK型无人机环境及边界性能试验方案",
    
    # 三轮互动配置
    "interactions": [
        {
            "id": 1,
            "chapter": "试验条件和参数设置",
            "question": "根据Matrice 350 RTK技术参数（工作温度：-20°C至50°C，防护等级：IP55）和GB/T 38924.3-2020标准，请确认以下高温试验参数：\n1. 高温贮存试验温度\n2. 高温工作试验温度（飞行器舱外）\n3. 试验持续时间",
            "reference_answer": "根据GB/T 38924.3-2020标准和设备技术参数：\n1. 高温贮存试验温度：70°C（按标准4.1.2推荐）\n2. 高温工作试验温度：55°C（飞行器舱外，按标准4.2.2推荐）\n3. 试验持续时间：\n   - 高温贮存试验：温度稳定后保持3小时\n   - 高温工作试验：温度稳定后保持2小时，之后工作至少2小时\n\n注意：本方案忽略GJB 150.3A-2009标准，完全依据GB/T 38924.3-2020标准。",
            "description": "确定高温试验基本参数"
        },
        {
            "id": 2,
            "chapter": "试验设备和试验步骤",
            "question": "根据GB/T 38924.3-2020标准，请设计高温试验所需的设备和详细步骤：\n1. 试验箱的技术要求\n2. 试验的具体步骤（高温贮存试验和高温工作试验）\n3. 安全防护措施",
            "reference_answer": "根据GB/T 38924.3-2020标准：\n1. 试验箱技术要求：\n   - 温控范围：-40°C~70°C，精度±1°C\n   - 试验箱容积与设备体积比≥5:1，发热设备≥10:1\n   - 受试设备与箱壁距离≥10cm\n   - 风速≤1.7m/s\n\n2. 试验步骤：\n   a) 高温贮存试验：试验前检测→温度调节至70°C→保持3h→恢复→试验后检测\n   b) 高温工作试验：试验前检测→温度调节至55°C→保持2h→功能测试2h→恢复→试验后检测\n\n3. 安全措施：试验箱接地保护、绝缘检测、应急泄压阀\n\n注意：本方案忽略GJB 150.3A-2009标准，完全依据GB/T 38924.3-2020标准。",
            "description": "设计试验设备和步骤"
        },
        {
            "id": 3,
            "chapter": "试验结果评定和报告",
            "question": "根据GB/T 38924.3-2020标准，请设计：\n1. 试验中断和恢复的处理方法\n2. 试验结果的评定标准\n3. 试验报告的内容要求",
            "reference_answer": "根据GB/T 38924.3-2020标准：\n1. 试验中断处理：\n   - 欠试验中断：从低于试验条件的点重新达到规定条件\n   - 过试验中断：宜停止试验，用新设备重新试验\n\n2. 试验结果评定：\n   - 高温贮存试验：试验前后外观和功能合格\n   - 高温工作试验：试验前、中、后功能合格\n\n3. 试验报告内容：\n   设备信息、试验依据、试验条件、试验过程、试验数据、试验结果、问题建议\n\n注意：本方案忽略GJB 150.3A-2009标准，完全依据GB/T 38924.3-2020标准。",
            "description": "设计结果评定和报告"
        }
    ],
    
    # 方案章节结构 - 修正章节编号，一级标题带序号，二级标题基于一级标题
    "chapters": [
        {
            "id": "chapter_1",
            "title": "1 范围",
            "level": 1,
            "content_template": "本试验方案规定了Matrice 350 RTK型无人机系统高温试验的试验条件、试验设备和仪器、试验过程、试验中断和恢复、试验结果评定和试验报告。本方案适用于在使用过程中可能会受到高温环境影响的无人机系统，以此来确定无人机系统对高温环境的耐受能力。\n\n注意：本方案依据GB/T 38924.3-2020标准，忽略GJB 150.3A-2009标准。"
        },
        {
            "id": "chapter_2",
            "title": "2 试验依据",
            "level": 1,
            "content_template": "1. GB/T 38924.1-2020 民用轻小型无人机系统环境试验方法 第1部分:总则\n2. GB/T 38924.3-2020 民用轻小型无人机系统环境试验方法 第3部分:高温试验\n3. Matrice 350 RTK型无人机技术参数手册\n\n注意：本方案忽略GJB 150.3A-2009标准，完全依据GB/T 38924.3-2020标准。"
        },
        {
            "id": "chapter_3",
            "title": "3 试验性质",
            "level": 1,
            "content_template": "本次试验为验证性高温环境适应性考核试验，为无人机在高温环境下的使用提供依据。"
        },
        {
            "id": "chapter_4",
            "title": "4 试验目的",
            "level": 1,
            "content_template": "考核Matrice 350 RTK型无人机在高温环境下的适应性和工作性能，发现高温环境适应性方面的问题缺陷，提出改进建议，为无人机在高温环境下的使用提供依据。"
        },
        {
            "id": "chapter_5",
            "title": "5 试验条件",
            "level": 1,
            "content_template": "## 5.1 高温贮存试验条件\n   - 试验温度：{storage_temp}°C（按GB/T 38924.3-2020 4.1.2推荐）\n   - 试验持续时间：受试设备达到温度稳定后再保持{storage_duration}\n   - 温度变化率：不大于3°C/min\n\n## 5.2 高温工作试验条件\n   - 试验温度：飞行器舱外部分{operating_temp}°C\n   - 试验持续时间：温度稳定后继续保持{operating_duration_pre}，之后工作至少{operating_duration_work}\n   - 温度变化率：同5.1.4\n\n注意：本方案依据GB/T 38924.3-2020标准，忽略GJB 150.3A-2009标准。"
        },
        {
            "id": "chapter_6",
            "title": "6 试验设备和仪器",
            "level": 1,
            "content_template": "## 6.1 试验箱技术要求\n   - 温控范围：-40°C~70°C，精度±1°C\n   - 试验箱容积与设备体积比≥5:1，发热设备≥10:1\n   - 受试设备与箱壁距离≥10cm\n   - 风速≤1.7m/s\n   - 温度检测系统精度为试验温度允许误差的1/3\n\n## 6.2 测试仪器要求\n   - 测量不确定度小于被测参数最大允许误差的1/3\n   - 所有仪器经检定/校准且在有效期内\n\n## 6.3 安全防护措施\n   - 试验箱接地保护\n   - 配备绝缘检测工具（兆欧表）\n   - 设置应急泄压阀\n   - 操作人员穿戴防护装备\n\n注意：本方案依据GB/T 38924.3-2020标准，忽略GJB 150.3A-2009标准。"
        },
        {
            "id": "chapter_7",
            "title": "7 试验过程",
            "level": 1,
            "content_template": "## 7.1 高温贮存试验过程\n   - 试验前准备：确定技术状态、温度稳定时间\n   - 预处理：正常大气条件下达到温度稳定\n   - 试验前检测：外观检查和功能检测\n   - 试验步骤：温度调节至{storage_temp}°C→保持{storage_duration}\n   - 恢复：恢复正常大气条件\n   - 试验后检测：重复功能检测\n\n## 7.2 高温工作试验过程\n   - 试验前准备：同高温贮存试验\n   - 预处理：正常大气条件下达到温度稳定\n   - 试验前检测：外观检查和功能检测\n   - 试验步骤：温度调节至{operating_temp}°C→保持{operating_duration_pre}→功能测试{operating_duration_work}\n   - 恢复：恢复正常大气条件\n   - 试验后检测：重复功能检测\n\n注意：本方案依据GB/T 38924.3-2020标准，忽略GJB 150.3A-2009标准。"
        },
        {
            "id": "chapter_8",
            "title": "8 试验中断和恢复",
            "level": 1,
            "content_template": "## 8.1 欠试验中断：当试验条件低于允差下限时，应从低于试验条件的点重新达到规定的试验条件，恢复试验直至结束。\n\n## 8.2 过试验中断：当试验条件超过允差上限时，宜停止试验，用新的受试设备重新试验。若分析确认受试设备未损坏，且证明过试验条件对该受试设备没有任何影响，则按8.1处理继续试验。\n\n注意：本方案依据GB/T 38924.3-2020标准，忽略GJB 150.3A-2009标准。"
        },
        {
            "id": "chapter_9",
            "title": "9 试验结果评定",
            "level": 1,
            "content_template": "## 9.1 高温贮存试验结果：受试设备在试验前、后的外观和结构检查结果以及功能/性能测试结果满足产品有关文件规定时，受试设备高温贮存试验合格。\n\n## 9.2 高温工作试验结果：受试设备在高温工作试验前、后的外观和结构检查结果以及试验前、中、后的功能/性能测试结果满足产品有关文件规定时，受试设备高温工作试验结果合格。\n\n注意：本方案依据GB/T 38924.3-2020标准，忽略GJB 150.3A-2009标准。"
        },
        {
            "id": "chapter_10",
            "title": "10 试验报告",
            "level": 1,
            "content_template": "试验报告应至少包括以下内容：\n   a) 受试设备型号、名称、组成、数量及提供单位的信息\n   b) 试验依据\n   c) 试验程序\n   d) 试验条件\n   e) 试验日期、试验地点、试验人员、试验设备、测试设备及仪器\n   f) 试验过程\n   g) 受试设备照片及其在试验设备中的安装照片\n   h) 试验参数控制或测量数据\n   i) 受试设备外观和功能/性能测试数据\n   j) 试验结果\n   k) 存在问题与建议\n\n注意：本方案依据GB/T 38924.3-2020标准，忽略GJB 150.3A-2009标准。"
        }
    ]
}

def get_sessions_store():
    """获取持久化会话存储"""
    try:
        sessions_dir = Path('./sessions')
        sessions_dir.mkdir(exist_ok=True)
        return shelve.open(str(sessions_dir / 'experiment_sessions.db'))
    except Exception as e:
        logger.error(f"获取会话存储失败: {e}")
        return None

def save_session(session_id, session_data):
    """保存会话到持久化存储"""
    try:
        store = get_sessions_store()
        if store:
            store[session_id] = session_data
            store.close()
        else:
            logger.error("无法获取会话存储")
    except Exception as e:
        logger.error(f"保存会话失败: {e}")

def load_session(session_id):
    """从持久化存储加载会话"""
    try:
        store = get_sessions_store()
        if store:
            data = store.get(session_id)
            store.close()
            return data
        else:
            logger.error("无法获取会话存储")
            return None
    except Exception as e:
        logger.error(f"加载会话失败: {e}")
        return None

def delete_session(session_id):
    """删除会话"""
    try:
        store = get_sessions_store()
        if store:
            if session_id in store:
                del store[session_id]
            store.close()
    except Exception as e:
        logger.error(f"删除会话失败: {e}")

def build_high_temp_system_prompt():
    """构建高温试验专用系统提示词"""
    return """你是一名专业的无人机环境试验方案设计专家，专门设计符合国家标准的高温试验方案。

你的任务是根据GB/T 38924.3-2020标准和Matrice 350 RTK无人机技术参数，设计专业的高温试验方案。

重要说明：本方案忽略GJB 150.3A-2009标准，完全依据GB/T 38924.3-2020标准。

专业知识要求：
1. 熟练掌握GB/T 38924.3-2020《民用轻小型无人机系统环境试验方法 第3部分：高温试验》标准
2. 了解Matrice 350 RTK无人机的技术参数：
   - 工作温度范围：-20°C 至 50°C
   - 防护等级：IP55
   - 最大起飞重量：9.2kg
   - 最大飞行海拔：7000m
3. 掌握高温试验的专业术语和试验方法

方案设计要求：
1. 格式必须严格遵循无人机试验方案模板结构
2. 内容必须符合GB/T 38924.3-2020标准要求 
3. 技术参数必须准确无误
4. 试验方法必须可操作、可执行
5. 使用专业、规范的技术术语
6. 在适当位置注明"注意：本方案依据GB/T 38924.3-2020标准，忽略GJB 150.3A-2009标准。"
7. 使用Markdown格式输出，便于前端渲染

你将通过3轮互动完成方案设计，每轮你会提出一个关键问题，然后根据标准答案生成方案的一部分内容。
最终整合所有部分，生成完整的《Matrice 350 RTK型无人机高温试验方案》。

现在开始执行任务。"""

def build_interaction_question_prompt(interaction_idx, session_data):
    """构建互动问题提示词"""
    interaction = HIGH_TEMP_CONFIG["interactions"][interaction_idx]
    
    prompt = f"""
    当前设计任务：Matrice 350 RTK型无人机高温试验方案
    文档标识：{session_data['metadata']['doc_id']}
    设备名称：{session_data['metadata']['device_name']}
    试验性质：{session_data['metadata']['test_purpose']}
    标准依据：GB/T 38924.3-2020 
    
    当前互动：第{interaction_idx + 1}轮 - {interaction['description']}

    问题：
    {interaction['question']}

    基于GB/T 38924.3-2020标准 ，请生成无人机高温试验方案的相关章节内容。

    生成要求：
    1. 只生成方案内容，不要包含问题和答案
    2. 使用专业的技术术语
    3. 符合GB/T 38924.3-2020标准 
    4. 内容详细、完整、准确
    5. 格式规范，使用Markdown格式，层次清晰
    6. 在适当位置注明"注意：本方案依据GB/T 38924.3-2020标准，忽略GJB 150.3A-2009标准。"

    请生成这部分方案内容："""

    return prompt

def create_high_temp_word_document(session_data, filepath):
    """创建高温试验方案Word文档"""
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        
        doc = Document()
        
        # 添加封面
        title = doc.add_heading(session_data['metadata']['doc_title'], 0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        doc.add_paragraph()
        doc.add_paragraph(f"文档标识：{session_data['metadata']['doc_id']}")
        doc.add_paragraph(f"设备型号：Matrice 350 RTK")
        doc.add_paragraph(f"参考标准：GB/T 38924.3-2020 ")
        doc.add_paragraph(f"编制单位：无人机环境试验中心")
        doc.add_paragraph(f"编制日期：{datetime.datetime.now().strftime('%Y年%m月%d日')}")
        
        doc.add_page_break()
        
        # 添加签署页
        doc.add_heading('签署页', level=1)
        doc.add_paragraph()
        doc.add_paragraph("编制：_________________")
        doc.add_paragraph("审核：_________________")
        doc.add_paragraph("会签：_________________")
        doc.add_paragraph("批准：_________________")
        
        doc.add_page_break()
        
        # 添加章节内容
        for chapter in session_data.get("generated_chapters", []):
            if chapter.get("title"):
                doc.add_heading(f"{chapter['title']}", level=1)
                
                if chapter.get("content"):
                    # 将Markdown格式转换为Word格式
                    content_lines = chapter["content"].split('\n')
                    for line in content_lines:
                        if line.strip():
                            # 处理Markdown标题
                            if line.startswith('# '):
                                doc.add_heading(line[2:], level=2)
                            elif line.startswith('## '):
                                doc.add_heading(line[3:], level=3)
                            else:
                                doc.add_paragraph(line.strip())
        
        # 保存文档
        doc.save(filepath)
        return True
    except Exception as e:
        logger.error(f"创建Word文档失败: {e}")
        return False

# ============ 方法1：分章节生成器（用于流式生成） ============

class SectionGenerator:
    """章节生成器类"""
    
    def __init__(self, session_id, session_data):
        self.session_id = session_id
        self.session_data = session_data
        self.sections = self._build_sections()
        self.current_section_index = 0
        self.generated_sections = []
        self.is_completed = False
        self.error = None
        
    def _build_sections(self):
        """构建章节列表 - 全部使用静态内容避免应用上下文问题"""
        sections = []
        
        # 定义所有章节的静态内容（使用Markdown格式，修正标题编号）
        static_sections = [
            {
                "id": "cover",
                "title": "封面",
                "type": "static",
                "content": f"""# Matrice 350 RTK型无人机高温试验方案

**文档标识**：{self.session_data['metadata']['doc_id']}
**设备型号**：Matrice 350 RTK
**参考标准**：GB/T 38924.3-2020 
**编制单位**：无人机环境试验中心
**编制日期**：{datetime.datetime.now().strftime('%Y年%m月%d日')}

---

*注意：本方案依据GB/T 38924.3-2020标准，忽略GJB 150.3A-2009标准。*"""
            },
            {
                "id": "signature",
                "title": "签署页",
                "type": "static",
                "content": """# 签署页

**编制**：_________________
**审核**：_________________
**会签**：_________________
**批准**：_________________

---

*注意：本方案依据GB/T 38924.3-2020标准，忽略GJB 150.3A-2009标准。*"""
            },
            {
                "id": "toc",
                "title": "目录",
                "type": "static",
                "content": """# 目录

## 1 范围
## 2 试验依据
## 3 试验性质
## 4 试验目的
## 5 试验条件
### 5.1 高温贮存试验条件
### 5.2 高温工作试验条件
## 6 试验设备和仪器
### 6.1 试验箱技术要求
### 6.2 测试仪器要求
### 6.3 安全防护措施
## 7 试验过程
### 7.1 高温贮存试验过程
### 7.2 高温工作试验过程
## 8 试验中断和恢复
### 8.1 欠试验中断
### 8.2 过试验中断
## 9 试验结果评定
### 9.1 高温贮存试验结果
### 9.2 高温工作试验结果
## 10 试验报告
## 11 试验组织及任务分工
### 11.1 试验组织
### 11.2 任务分工
## 12 风险分析与相关措施
### 12.1 主要风险
### 12.2 应对措施
## 附录
### 附录A 试验参数表
### 附录B 试验设备清单
"""
            }
        ]
            
        # 添加已生成的章节（使用Markdown格式）
        for chapter in self.session_data.get("generated_chapters", []):
            if chapter.get("title"):
                static_sections.append({
                    "id": chapter["id"],
                    "title": chapter["title"],
                    "type": "static",
                    "content": chapter.get("content", "")
                })
        
        # 添加补充章节（使用Markdown格式，修正编号）
        additional_chapters = [
            {
                "id": "org_task",
                "title": "11 试验组织及任务分工",
                "type": "static",
                "content": """# 11 试验组织及任务分工

## 11.1 试验组织
试验组由试验负责人、试验工程师、操作人员、安全员等组成。

## 11.2 任务分工
- **试验负责人**：负责试验的全面管理和协调
- **试验工程师**：负责试验方案实施和技术指导
- **操作人员**：负责试验设备操作和记录
- **安全员**：负责试验现场安全监督

---

*注意：本方案依据GB/T 38924.3-2020标准，忽略GJB 150.3A-2009标准。*"""
            },
            {
                "id": "risk_analysis",
                "title": "12 风险分析与相关措施",
                "type": "static",
                "content": """# 12 风险分析与相关措施

## 12.1 主要风险
- **高温设备烫伤风险**：试验箱温度可达70°C，接触可能导致烫伤
- **设备过热损坏风险**：试验设备在高温环境下可能发生故障
- **试验中断风险**：电源故障、设备故障等导致试验中断
- **数据记录错误风险**：人工记录数据可能出错

## 12.2 应对措施
- **佩戴防护装备**：操作人员必须穿戴隔热手套、防护服
- **设置温度监控报警**：实时监控温度，设置超温报警
- **制定应急处理预案**：制定详细的应急处理流程
- **双重数据记录核对**：采用自动记录和人工记录双重备份

---

*注意：本方案依据GB/T 38924.3-2020标准，忽略GJB 150.3A-2009标准。*"""
            },
            {
                "id": "appendix",
                "title": "附录",
                "type": "static",
                "content": """# 附录

## 附录A 试验参数表

| 参数项 | 试验条件 | 参考标准 |
|--------|----------|----------|
| 高温贮存温度 | 70°C | GB/T 38924.3-2020 4.1.2 |
| 高温贮存时间 | 3h | GB/T 38924.3-2020 4.1.2 |
| 高温工作温度 | 55°C | GB/T 38924.3-2020 4.2.2 |
| 高温工作预热时间 | 2h | GB/T 38924.3-2020 4.2.2 |
| 高温工作测试时间 | 2h | GB/T 38924.3-2020 4.2.2 |

## 附录B 试验设备清单

### B.1 主要试验设备
1. **高低温试验箱**：温控范围 -40°C~70°C，精度±1°C
2. **温度记录仪**：精度±0.5°C，带自动记录功能
3. **多功能测试系统**：用于无人机功能检测
4. **兆欧表**：用于绝缘检测，量程0-5000MΩ

### B.2 辅助设备
1. 应急泄压阀
2. 绝缘检测工具
3. 防护装备（隔热手套、防护服等）
4. 灭火器材

---

*注意：本方案依据GB/T 38924.3-2020标准，忽略GJB 150.3A-2009标准。*"""
            }
        ]
            
        static_sections.extend(additional_chapters)
        return static_sections

    def generate_next_section(self):
        """生成下一个章节"""
        if self.current_section_index >= len(self.sections):
            self.is_completed = True
            return None
        
        section = self.sections[self.current_section_index]
        
        try:
            if section["type"] == "static":
                content = section["content"]
            else:
                content = f"# {section['title']}\n\n内容生成中..."
            
            result = {
                "id": section["id"],
                "title": section["title"],
                "content": content,
                "index": self.current_section_index,
                "total": len(self.sections),
                "timestamp": datetime.datetime.now().isoformat()
            }
            
            self.generated_sections.append(result)
            self.current_section_index += 1
            
            # 如果所有章节都生成完成
            if self.current_section_index >= len(self.sections):
                self.is_completed = True
            
            return result
            
        except Exception as e:
            self.error = str(e)
            logger.error(f"生成章节失败 {section['id']}: {e}")
            return None
    
    def get_full_content(self):
        """获取完整内容"""
        full_content = ""
        for section in self.generated_sections:
            full_content += f"\n\n{section['content']}"
        return full_content

# ============ 方法2：流式生成器 ============

class StreamGenerator:
    """流式生成器"""
    
    def __init__(self, session_id, session_data):
        self.session_id = session_id
        self.session_data = session_data
        self.generator = SectionGenerator(session_id, session_data)
    
    def generate_stream(self):
        """生成流式响应"""
        def event_stream():
            total_sections = len(self.generator.sections)
            
            # 发送开始事件
            start_event = {
                "type": "start",
                "total_sections": total_sections,
                "timestamp": datetime.datetime.now().isoformat()
            }
            yield f"data: {json.dumps(start_event)}\n\n"
            
            # 生成每个章节
            for i, section in enumerate(self.generator.sections):
                # 模拟生成延迟
                time.sleep(0.5)
                
                # 发送进度事件
                progress_event = {
                    "type": "progress",
                    "current": i + 1,
                    "total": total_sections,
                    "section": section["title"]
                }
                yield f"data: {json.dumps(progress_event)}\n\n"
                
                # 获取章节内容
                content = section.get("content", f"# {section['title']}\n\n内容生成中...")
                
                # 发送章节开始事件（包含章节标题）
                section_start_event = {
                    "type": "section_start",
                    "section_id": section["id"],
                    "section_title": section["title"],
                    "content_type": "markdown"
                }
                yield f"data: {json.dumps(section_start_event)}\n\n"
                
                # 分块发送内容（模拟打字机效果）
                lines = content.split('\n')
                for line_num, line in enumerate(lines):
                    if line.strip():  # 只发送非空行
                        # 添加适当的延迟
                        time.sleep(0.1)
                        
                        # 如果是标题行，稍微增加延迟
                        if line.startswith('#') or line.startswith('##') or line.startswith('###'):
                            time.sleep(0.2)
                        
                        # 发送行内容
                        content_event = {
                            "type": "content",
                            "section_id": section["id"],
                            "section_title": section["title"],
                            "chunk": line + '\n',  # 保持换行符
                            "is_new_line": True,
                            "line_number": line_num + 1,
                            "total_lines": len(lines)
                        }
                        yield f"data: {json.dumps(content_event)}\n\n"
                
                # 发送章节完成事件
                section_complete_event = {
                    "type": "section_complete",
                    "section_id": section["id"],
                    "section_title": section["title"],
                    "index": i,
                    "current": i + 1,
                    "total": total_sections,
                    "timestamp": datetime.datetime.now().isoformat()
                }
                yield f"data: {json.dumps(section_complete_event)}\n\n"
                
                # 章节间延迟
                time.sleep(0.3)
            
            # 发送完成事件
            complete_event = {
                "type": "complete",
                "total_generated": total_sections,
                "timestamp": datetime.datetime.now().isoformat()
            }
            yield f"data: {json.dumps(complete_event)}\n\n"
           
        return Response(
            event_stream(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

# ============ 主要API端点 ============

@experiment_bp.route('/start', methods=['POST'])
def start_high_temp_experiment():
    """开始高温试验方案设计"""
    try:
        data = request.get_json()
        domain = data.get('domain', '无人机领域')
        
        # 添加8秒延时，模拟思考时间
        logger.info("start接口开始处理，添加8秒延时...")
        time.sleep(1)
        
        # 创建唯一会话ID
        session_id = f"high_temp_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(4).hex()}"
        
        # 初始化会话
        session_data = {
            "domain": domain,
            "metadata": {
                "doc_id": HIGH_TEMP_CONFIG["doc_id"],
                "doc_title": HIGH_TEMP_CONFIG["doc_title"],
                "device_name": HIGH_TEMP_CONFIG["device_name"],
                "standard": "GB/T 38924.3-2020 ",
                "temperature_range": "-20°C 至 50°C",
                "protection_level": "IP55",
                "test_purpose": data.get('step1', {}).get('testPurpose', 'qualification'),
                "test_type": data.get('step2', {}).get('testType', ['temperature']),
                "standard_system": "国标（忽略国军标）"
            },
            "chapters": HIGH_TEMP_CONFIG["chapters"],
            "current_interaction": 0,
            "interaction_history": [],
            "generated_chapters": [],
            "created_at": datetime.datetime.now().isoformat(),
            "last_activity": datetime.datetime.now().isoformat()
        }
        
        # 保存会话
        save_session(session_id, session_data)
        
        logger.info(f"创建新会话: {session_id}")
        
        # 获取第一个互动问题
        first_interaction = HIGH_TEMP_CONFIG["interactions"][0]
        
        # 构建问题列表
        questions = [{
            "id": "q1",
            "text": first_interaction["question"],
            "type": "text",
            "hint": "请参考GB/T 38924.3-2020标准 ",
            "reference_answer": first_interaction["reference_answer"]
        }]
        
        # 确定第一个章节
        first_chapter = HIGH_TEMP_CONFIG["chapters"][0]
        
        return jsonify({
            "success": True,
            "sessionId": session_id,
            "metadata": {
                "title": HIGH_TEMP_CONFIG["doc_title"],
                "device": HIGH_TEMP_CONFIG["device_name"],
                "standard": "GB/T 38924.3-2020 "
            },
            "questions": questions,
            "currentChapter": {
                "id": first_chapter["id"],
                "title": first_chapter["title"],
                "level": first_chapter["level"]
            },
            "progress": {
                "current": 1,
                "total": len(HIGH_TEMP_CONFIG["interactions"])
            },
            "instruction": "请输入任意回答，系统将根据预设答案生成方案内容。注意：本方案依据GB/T 38924.3-2020标准，忽略GJB 150.3A-2009标准。"
        })
        
    except Exception as e:
        logger.exception("start_high_temp_experiment error")
        return jsonify({
            "success": False,
            "error": str(e),
            "detail": "高温试验方案初始化失败"
        }), 500

@experiment_bp.route('/answer', methods=['POST'])
def submit_high_temp_answer():
    """提交答案并生成下一部分"""
    try:
        data = request.get_json()
        session_id = data.get('sessionId')
        
        logger.info(f"提交答案 - Session ID: {session_id}")
        
        if not session_id:
            return jsonify({"success": False, "error": "缺少会话ID"}), 400
        
        # 添加8秒延时，模拟思考时间
        logger.info("answer接口开始处理，添加8秒延时...")
        time.sleep(1)
        
        # 加载会话数据
        session_data = load_session(session_id)
        if not session_data:
            return jsonify({
                "success": False, 
                "error": "会话已过期或不存在",
                "code": "SESSION_EXPIRED",
                "action": "restart"
            }), 400
        
        current_interaction_idx = session_data.get("current_interaction", 0)
        
        # 检查互动索引是否有效
        if current_interaction_idx >= len(HIGH_TEMP_CONFIG["interactions"]):
            # 所有互动完成，开始流式生成
            return jsonify({
                "success": True,
                "action": "start_stream_generation",
                "message": "开始生成完整试验方案",
                "stream_url": f"/api/experiment/stream-generate?sessionId={session_id}",
                "sessionId": session_id
            })
        
        # 获取当前互动配置
        interaction_config = HIGH_TEMP_CONFIG["interactions"][current_interaction_idx]
        
        # 处理答案数据
        answers_data = data.get('answers', {})
        user_answer = "确认"
        
        if isinstance(answers_data, dict) and answers_data:
            user_answer = list(answers_data.values())[0]
        
        # 记录互动历史
        interaction_record = {
            "interaction_id": current_interaction_idx + 1,
            "question": interaction_config["question"],
            "user_answer": user_answer,
            "reference_answer": interaction_config["reference_answer"],
            "timestamp": datetime.datetime.now().isoformat()
        }
        
        # 根据互动索引确定要生成的章节
        chapter_ranges = [
            [0, 4],   # 第1轮：章节0-3（范围、试验依据、试验性质、试验目的）
            [4, 7],   # 第2轮：章节4-6（试验条件、试验设备和仪器、试验过程）
            [7, 10]   # 第3轮：章节7-9（试验中断和恢复、试验结果评定、试验报告）
        ]
        
        chapter_range = chapter_ranges[current_interaction_idx]
        start_idx, end_idx = chapter_range
        
        # 生成对应的章节内容
        generated_chapters = []
        for idx in range(start_idx, end_idx):
            if idx < len(HIGH_TEMP_CONFIG["chapters"]):
                chapter_template = HIGH_TEMP_CONFIG["chapters"][idx]
                
                # 替换模板中的参数
                content = chapter_template["content_template"]
                content = content.replace("{storage_temp}", "70")
                content = content.replace("{storage_duration}", "3h")
                content = content.replace("{operating_temp}", "55")
                content = content.replace("{operating_duration_pre}", "2h")
                content = content.replace("{operating_duration_work}", "2h")
                
                # 确保内容以标题开始（Markdown格式）
                if not content.startswith('# '):
                    # 一级标题使用#，二级标题使用##
                    content = f"# {chapter_template['title']}\n\n{content}"
                
                generated_chapters.append({
                    "id": chapter_template["id"],
                    "title": chapter_template["title"],
                    "level": chapter_template["level"],
                    "content": content,
                    "content_type": "markdown"
                })
        
        # 保存生成的内容
        session_data["generated_chapters"].extend(generated_chapters)
        interaction_record["generated_chapters"] = generated_chapters
        if generated_chapters:
            # 提取章节内容的前几行作为预览
            preview_content = generated_chapters[0]["content"]
            preview_lines = preview_content.split('\n')[:5]
            interaction_record["content_preview"] = '\n'.join(preview_lines) + "..."
        else:
            interaction_record["content_preview"] = ""
        
        session_data["interaction_history"].append(interaction_record)
        
        # 更新互动索引
        next_interaction_idx = current_interaction_idx + 1
        session_data["current_interaction"] = next_interaction_idx
        
        # 保存会话
        save_session(session_id, session_data)
        
        if next_interaction_idx < len(HIGH_TEMP_CONFIG["interactions"]):
            # 准备下一轮互动
            next_interaction = HIGH_TEMP_CONFIG["interactions"][next_interaction_idx]
            
            # 确定下一个章节
            next_chapter_range = chapter_ranges[next_interaction_idx]
            next_chapter_idx = next_chapter_range[0]
            next_chapter = HIGH_TEMP_CONFIG["chapters"][next_chapter_idx] if next_chapter_idx < len(HIGH_TEMP_CONFIG["chapters"]) else HIGH_TEMP_CONFIG["chapters"][0]
            
            # 构建下一个问题
            next_questions = [{
                "id": "q1",
                "text": next_interaction["question"],
                "type": "text",
                "hint": "请参考GB/T 38924.3-2020标准 ",
                "reference_answer": next_interaction["reference_answer"]
            }]
            
            return jsonify({
                "success": True,
                "action": "next_interaction",
                "preview": interaction_record["content_preview"],
                "questions": next_questions,
                "currentChapter": {
                    "id": next_chapter["id"],
                    "title": next_chapter["title"],
                    "level": next_chapter["level"]
                },
                "progress": {
                    "current": next_interaction_idx + 1,
                    "total": len(HIGH_TEMP_CONFIG["interactions"])
                },
                "instruction": f"第{next_interaction_idx + 1}轮互动，请输入任意回答。注意：本方案依据GB/T 38924.3-2020标准，忽略GJB 150.3A-2009标准。",
                "referenceAnswer": next_interaction["reference_answer"],
                "format_note": "方案内容将以Markdown格式生成，便于前端渲染显示。"
            })
        else:
            # 所有互动完成，开始流式生成
            return jsonify({
                "success": True,
                "action": "start_stream_generation",
                "message": "开始生成完整试验方案",
                "stream_url": f"/api/experiment/stream-generate?sessionId={session_id}",
                "sessionId": session_id,
                "format_note": "方案将以Markdown格式流式生成，包含完整章节结构。"
            })
            
    except Exception as e:
        logger.exception("submit_high_temp_answer error")
        return jsonify({
            "success": False,
            "error": str(e),
            "detail": "答案处理失败"
        }), 500

@experiment_bp.route('/stream-generate', methods=['GET'])
def stream_generate():
    """流式生成完整报告"""
    try:
        session_id = request.args.get('sessionId')
        
        if not session_id:
            return jsonify({"success": False, "error": "缺少会话ID"}), 400
        
        # 加载会话数据
        session_data = load_session(session_id)
        if not session_data:
            return jsonify({"success": False, "error": "会话不存在"}), 404
        
        # 创建流式生成器
        stream_generator = StreamGenerator(session_id, session_data)
        
        # 开始流式生成
        return stream_generator.generate_stream()
        
    except Exception as e:
        logger.error(f"流式生成失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@experiment_bp.route('/generation-status', methods=['GET'])
def get_generation_status():
    """获取生成状态（轮询接口）"""
    try:
        session_id = request.args.get('sessionId')
        
        if not session_id:
            return jsonify({"success": False, "error": "缺少会话ID"}), 400
        
        # 加载会话数据
        session_data = load_session(session_id)
        if not session_data:
            return jsonify({"success": False, "error": "会话不存在"}), 404
        
        # 检查是否在生成中
        generator = section_generators.get(session_id)
        
        if not generator:
            # 如果没有生成器，检查是否已经完成
            if session_data.get("full_content"):
                return jsonify({
                    "success": True,
                    "status": "completed",
                    "full_content": session_data.get("full_content"),
                    "download_url": session_data.get("download_url"),
                    "filename": session_data.get("filename"),
                    "content_type": "markdown"
                })
            else:
                return jsonify({
                    "success": True,
                    "status": "not_started",
                    "message": "生成尚未开始"
                })
        
        # 获取进度信息
        progress = {
            "current": generator.current_section_index,
            "total": len(generator.sections),
            "percentage": int((generator.current_section_index / len(generator.sections)) * 100) if generator.sections else 0
        }
        
        if generator.is_completed:
            # 生成完成
            full_content = generator.get_full_content()
            
            # 保存完整内容
            session_data["full_content"] = full_content
            
            # 生成下载文件（Markdown格式）
            output_dir = current_app.config.get('OUTPUT_DIR', './output')
            os.makedirs(output_dir, exist_ok=True)
            
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"Matrice350RTK_高温试验方案_{timestamp}.md"
            filepath = os.path.join(output_dir, filename)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(full_content)
            
            # 同时生成Word文档
            word_filename = f"Matrice350RTK_高温试验方案_{timestamp}.docx"
            word_filepath = os.path.join(output_dir, word_filename)
            create_high_temp_word_document(session_data, word_filepath)
            
            session_data["download_url"] = f"/api/experiment/download?filename={filename}"
            session_data["filename"] = filename
            session_data["word_download_url"] = f"/api/experiment/download?filename={word_filename}"
            session_data["word_filename"] = word_filename
            
            save_session(session_id, session_data)
            
            # 清理生成器
            del section_generators[session_id]
            
            return jsonify({
                "success": True,
                "status": "completed",
                "progress": progress,
                "full_content": full_content,
                "download_url": session_data["download_url"],
                "filename": session_data["filename"],
                "word_download_url": session_data["word_download_url"],
                "word_filename": session_data["word_filename"],
                "content_type": "markdown"
            })
        
        elif generator.error:
            # 生成失败
            del section_generators[session_id]
            return jsonify({
                "success": False,
                "status": "failed",
                "error": generator.error
            })
        else:
            # 生成中
            current_section = None
            if generator.current_section_index < len(generator.sections):
                current_section = generator.sections[generator.current_section_index]["title"]
            
            return jsonify({
                "success": True,
                "status": "in_progress",
                "progress": progress,
                "current_section": current_section,
                "generated_sections": generator.generated_sections,
                "message": f"正在生成 {current_section or '下一章节'}...",
                "content_type": "markdown"
            })
            
    except Exception as e:
        logger.error(f"获取生成状态失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@experiment_bp.route('/generate-report', methods=['POST'])
def generate_report():
    """非流式生成报告（批量生成）"""
    try:
        data = request.get_json()
        session_id = data.get('sessionId')
        
        if not session_id:
            return jsonify({"success": False, "error": "缺少会话ID"}), 400
        
        # 加载会话数据
        session_data = load_session(session_id)
        if not session_data:
            return jsonify({"success": False, "error": "会话不存在"}), 404
        
        # 创建章节生成器
        generator = SectionGenerator(session_id, session_data)
        section_generators[session_id] = generator
        
        # 启动生成线程
        def generate_in_thread():
            try:
                with current_app.app_context():
                    while not generator.is_completed:
                        generator.generate_next_section()
                        time.sleep(1)  # 控制生成速度
                        
                        # 每生成3个章节保存一次进度
                        if len(generator.generated_sections) % 3 == 0:
                            session_data = load_session(session_id)
                            if session_data:
                                session_data["generation_progress"] = {
                                    "sections": generator.generated_sections,
                                    "current": generator.current_section_index,
                                    "total": len(generator.sections)
                                }
                                save_session(session_id, session_data)
            
            except Exception as e:
                logger.error(f"生成线程异常: {e}")
                generator.error = str(e)
        
        # 启动线程
        thread = threading.Thread(target=generate_in_thread)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            "success": True,
            "message": "开始生成报告",
            "status_url": f"/api/experiment/generation-status?sessionId={session_id}",
            "stream_url": f"/api/experiment/stream-generate?sessionId={session_id}",
            "estimated_time": "约2-3分钟",
            "format_note": "报告将以Markdown格式生成，支持前端渲染。"
        })
        
    except Exception as e:
        logger.error(f"生成报告失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@experiment_bp.route('/download', methods=['GET'])
def download_high_temp_report():
    """下载高温试验报告"""
    try:
        filename = request.args.get('filename')
        if not filename:
            return jsonify({"success": False, "error": "缺少文件名"}), 400
        
        output_dir = current_app.config.get('OUTPUT_DIR', './output')
        filepath = os.path.join(output_dir, filename)
        
        if not os.path.exists(filepath):
            return jsonify({"success": False, "error": "文件不存在"}), 404
        
        from flask import send_from_directory
        return send_from_directory(output_dir, filename, as_attachment=True)
        
    except Exception as e:
        logger.exception("download_high_temp_report error")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

# ============ 启动函数 ============

def start_generation_task(session_id):
    """启动生成任务（供其他函数调用）"""
    try:
        session_data = load_session(session_id)
        if not session_id:
            return False
        
        # 创建生成器
        generator = SectionGenerator(session_id, session_data)
        section_generators[session_id] = generator
        
        return True
        
    except Exception as e:
        logger.error(f"启动生成任务失败: {e}")
        return False