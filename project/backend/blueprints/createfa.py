from flask import Blueprint, request, jsonify, current_app
import uuid
import os
import re
import json
import hashlib

CC_bp = Blueprint('CreateFa', __name__, url_prefix='/api/fa')



SESSIONS = {}
TEMPLATE_CACHE = {}  # 缓存已分析的模板

def read_template_file():
    """读取模板文件并进行分析"""
    template_path = 'public/Syfatext/template_analysis.txt'
    try:
        with open(template_path, 'r', encoding='utf-8') as file:
            return file.read()
        
    except FileNotFoundError:
        base_template = "# 试验方案模板\n\n## 1. 试验目的\n\n## 2. 试验原理\n\n## 3. 试验设备与材料\n\n## 4. 试验步骤\n\n## 5. 数据记录与处理\n\n## 6. 安全注意事项\n\n## 7. 参考文献"
        return base_template
    except Exception as e:
        return f"读取模板文件时出错: {str(e)}"



# def extract_test_processes(selected_tests):
#     """从选中的试验标准中提取试验过程内容，并识别不同的试验项目"""
#     process_contents = []
#     nodes = []
    
#     # 确保 selected_tests 是字典
#     if not isinstance(selected_tests, dict):
#         return {"content": "", "nodes": []}
    
#     for std_key, std_data in selected_tests.items():
#         if isinstance(std_data, dict) and std_data.get("success") and "content" in std_data:
#             content = std_data["content"]
            
#             # 添加节点数据（如果存在）
#             if "nodes" in std_data:
#                 nodes.extend(std_data["nodes"])  # 使用 extend 而不是 append
            
#             # 提取标准编号（从键名中提取）
#             std_code = std_key.replace("std_", "")
            
#             # 尝试提取试验过程部分
#             if "试验过程" in content:
#                 # 提取从"试验过程"开始到文档末尾的内容
#                 process_start = content.find("# 试验过程")
#                 if process_start != -1:
#                     process_content = content[process_start:]
                    
#                     # 添加标准标识，帮助模型识别不同标准的试验过程
#                     process_content = f"## 标准来源: {std_code}\n\n{process_content}"
#                     process_contents.append(process_content)
#                 else:
#                     # 添加标准标识
#                     process_content = f"## 标准来源: {std_code}\n\n{content}"
#                     process_contents.append(process_content)
#             else:
#                 # 添加标准标识
#                 process_content = f"## 标准来源: {std_code}\n\n{content}"
#                 process_contents.append(process_content)
    
#     # 返回合并后的内容，每个标准之间用分隔线隔开
#     return {
#         "content": "\n\n---\n\n".join(process_contents),
#         "nodes": nodes
#     }


def extract_test_processes(selected_tests):
    """从选中的试验标准中提取试验过程内容，并识别不同的试验项目"""
    print("执行 extract_test_processes 函数")
    print(f"selected_tests 类型: {type(selected_tests)}, 内容: {selected_tests}")
    
    process_contents = []
    nodes = []
    
    # 确保 selected_tests 是字典
    if not isinstance(selected_tests, dict):
        print(f"错误: selected_tests 不是字典，类型: {type(selected_tests)}")
        return {"content": "", "nodes": []}
    
    print(f"selected_tests 键: {list(selected_tests.keys())}")
    
    for std_key, std_data in selected_tests.items():
        print(f"处理标准: {std_key}")
        
        # 检查是否有数据且成功获取
        if isinstance(std_data, dict) and std_data.get("success"):
            print(f"标准数据有效: {std_key}")
            
            # 获取标准编号，去除前缀
            std_code = std_key.replace("std_", "").replace("—", "-")  # 统一分隔符
            
            # 检查record中的clauseName是否为"试验过程"
            record_info = std_data.get("record", {})
            clause_name = record_info.get("clauseName", "")
            
            print(f"标准: {std_code}, clause_name: {clause_name}")
            
            # 只有当clauseName是"试验过程"时才处理
            if clause_name != "试验过程":
                print(f"跳过非试验过程: {clause_name}")
                continue  # 跳过非试验过程的内容
            
            # 添加节点数据（如果存在）
            if "nodes" in std_data and isinstance(std_data["nodes"], list):
                # 只添加当前标准的nodes
                current_nodes = std_data["nodes"]
                print(f"找到 {len(current_nodes)} 个节点")
                
                # 从nodes中提取条款内容
                clause_contents = []
                for i, node in enumerate(current_nodes):
                    if isinstance(node, dict):
                        node_name = node.get("name", "")
                        node_number = node.get("number", "")
                        node_content = node.get("content", "")
                        
                        print(f"节点 {i}: name={node_name}, number={node_number}")
                        
                        # 如果节点有content，使用它；否则使用definition或生成占位内容
                        if node_content:
                            content_text = node_content
                        else:
                            definition = node.get("definition", "")
                            if definition:
                                content_text = definition
                            else:
                                content_text = f"{node_name}的具体试验步骤和要求"
                        
                        clause_contents.append(f"### {node_number} {node_name}\n\n{content_text}")
                
                if clause_contents:
                    # 构建完整的试验过程内容
                    process_content = f"## 标准来源: {std_code}\n\n# 试验过程\n\n" + "\n\n".join(clause_contents)
                    process_contents.append(process_content)
                    print(f"添加试验过程内容，长度: {len(process_content)}")
                    
                    # 将nodes添加到总列表中
                    nodes.extend(current_nodes)
            else:
                print(f"标准 {std_key} 没有nodes或nodes不是列表")
    
    print(f"总共提取了 {len(process_contents)} 个试验过程")
    print(f"总共提取了 {len(nodes)} 个节点")
    
    # 如果没有提取到内容，返回空
    if not process_contents:
        print("没有提取到试验过程内容")
        return {"content": "", "nodes": nodes}
    
    # 返回合并后的内容，每个标准之间用分隔线隔开
    result = {
        "content": "\n\n---\n\n".join(process_contents),
        "nodes": nodes
    }
    print(f"返回结果，content长度: {len(result['content'])}, nodes数量: {len(result['nodes'])}")
    
    return result

@CC_bp.route("/createfa", methods=["POST"])
def index():
    try:
        print("开始处理 /api/fa/createfa 请求")
        data = request.get_json()
        print(f"接收到的数据: {data}")
        
        if not data:
            print("错误: 请求数据为空")
            return jsonify({"success": False, "error": "请求数据为空"}), 400
        
        details = data.get("selectedMethodDetails", {})
        message = data.get("message", "请根据试验标准和模板生成完整的试验方案")
        session_id = data.get("sessionId")
        selected_tests = data.get("selectedTests", {})
        
        print(f"details 类型: {type(details)}, 内容: {details}")
        print(f"selected_tests 类型: {type(selected_tests)}, 内容: {selected_tests}")
        
        # 检查openai_client是否可用
        if openai_client is None:
            print("错误: openai_client 未初始化")
            return jsonify({"success": False, "error": "AI服务未正确初始化"}), 500
        
        print("正在读取模板文件...")
        # 读取模板文件
        template = read_template_file()
        print(f"模板内容长度: {len(template)}")
        
        print("正在提取试验过程...")
        # 从选中的试验标准中提取试验过程
        process_data = extract_test_processes(selected_tests)
        print(f"提取的试验过程数据: {process_data}")
        
        print("正在格式化节点数据...")
        # 将 nodes 转换为适合嵌入提示词的格式
        nodes_str = format_nodes_for_prompt(process_data.get("nodes", []))
        print(f"格式化后的节点数据长度: {len(nodes_str)}")
        
        # 将details转换为字符串格式
        if isinstance(details, tuple):
            # 如果details是元组（可能是由于逗号导致的），取第一个元素
            details = details[0] if details else {}
        
        if isinstance(details, dict):
            details_str = json.dumps(details, ensure_ascii=False)
        else:
            details_str = str(details)
            
        print(f"details_str 长度: {len(details_str)}")
        
        print("正在调用get_response...")
        res, session_id = get_response(
            details=details_str,
            message=message, 
            session_id=session_id,
            template=template,
            content=process_data.get("content", ""), 
            nodes=nodes_str
        )
        
        print(f"API响应长度: {len(res)}")
        return jsonify({"success": True, "response": res, "sessionId": session_id})
        
    except Exception as e:
        error_msg = f"处理请求时出错: {str(e)}"
        print(f"路由错误详情: {error_msg}")
        import traceback
        print(f"详细堆栈信息: {traceback.format_exc()}")
        return jsonify({"success": False, "error": error_msg}), 500
def format_nodes_for_prompt(nodes):
    """将节点列表格式化为适合嵌入提示词的字符串"""
    if not nodes:
        return "无节点数据"
    
    nodes_str = ""
    for i, node in enumerate(nodes, 1):
        nodes_str += f"节点 {i}:\n"
        if isinstance(node, dict):
            for key, value in node.items():
                nodes_str += f"  {key}: {value}\n"
        else:
            nodes_str += f"  {node}\n"
        nodes_str += "\n"
    
    return nodes_str

def get_response(details, message, session_id=None, template="", content="", nodes=""):
    print(f"进入get_response函数")
    print(f"details类型: {type(details)}, 长度: {len(str(details))}")
    print(f"message: {message}")
    print(f"session_id: {session_id}")
    print(f"template长度: {len(template)}")
    print(f"content长度: {len(content)}")
    print(f"nodes长度: {len(nodes)}")
    
    # 首先检查 openai_client 是否已初始化
    if openai_client is None:
        error_msg = "服务初始化失败: OpenAI客户端未正确配置"
        print(error_msg)
        return error_msg, session_id or str(uuid.uuid4())
    
    try:
        # 设计专门的系统提示词
        system_prompt = """您是一位专业的试验方案编写专家，具有丰富的标准文档编写经验。请根据提供的试验方案模板和相关标准中的试验过程，生成一个完整、规范的专业试验方案。

## 模板参考
这是一个完整的试验方案模板，请严格遵循其文档结构和格式规范，只是参考不是照抄：
{template}

## 引用标准参考
以下是选中的试验标准详细信息：
{details}

## 试验过程参考
以下是相关标准中的试验过程内容，请将其整合到模板的相应部分：
{content}

## 试验结果评定参考
以下是相关标准中的试验结果评定内容，数据是json格式，请拆解语句理解，并将其分散整合到模板的相应部分：
{nodes}

## 编写要求
1. **严格遵循模板结构**：完全按照模板的章节编号体系和文档结构
2. **试验过程整合**：将提供的试验过程内容整合到"试验内容及方法"章节中
3. **专业规范**：使用标准化的科技文档语言，保持术语一致性
4. **内容完整性**：确保包含模板中的所有必要章节

请基于以上要求，生成一个专业、完整、规范的试验方案文档。输出必须是完整的Markdown格式文档。"""
        
        # 格式化系统提示词，确保参数不为None
        formatted_system_prompt = system_prompt.format(
            template=template[:20000] if template else "",
            details=str(details)[:2000] if details else "",
            content=str(content)[:2000] if content else "",
            nodes=str(nodes)[:2000] if nodes else ""
        )
        
        print(f"系统提示词长度: {len(formatted_system_prompt)}")
        
        if not session_id or session_id not in SESSIONS:
            session_id = str(uuid.uuid4())
            SESSIONS[session_id] = {"messages": [{"role": "system", "content": formatted_system_prompt}]}
            print(f"创建新会话: {session_id}")
        else:
            # 如果已有会话，确保系统提示词是最新的
            print(f"使用现有会话: {session_id}")
            if SESSIONS[session_id]["messages"][0]["role"] == "system":
                SESSIONS[session_id]["messages"][0]["content"] = formatted_system_prompt
            else:
                SESSIONS[session_id]["messages"].insert(0, {"role": "system", "content": formatted_system_prompt})

        session = SESSIONS[session_id]
        session["messages"].append({"role": "user", "content": message})
        
        print(f"会话消息数: {len(session['messages'])}")

        try:
            print(f"正在调用OpenAI API，模型: qwen-plus")
            print(f"第一条消息: {session['messages'][0]['content'][:100]}...")
            
            completion = openai_client.chat.completions.create(
                model="qwen-plus",
                messages=session["messages"],
                temperature=0.3,
                stream=False
            )
            
            print("API调用成功")
            res = completion.choices[0].message.content
            
            # 确保响应是Markdown格式
            if res:
                res = ensure_markdown_format(res, template)
            else:
                res = "# 试验方案\n\n抱歉，未能生成试验方案内容。"
            
            session["messages"].append({"role": "assistant", "content": res})
            print(f"响应生成完成，长度: {len(res)}")
            
            return res, session_id
            
        except Exception as api_error:
            error_msg = f"调用AI模型时出错: {str(api_error)}"
            print(f"API调用错误: {error_msg}")
            import traceback
            print(f"API错误堆栈: {traceback.format_exc()}")
            return error_msg, session_id
            
    except Exception as e:
        error_msg = f"处理请求时出错: {str(e)}"
        print(f"get_response函数错误: {error_msg}")
        import traceback
        print(f"详细堆栈信息: {traceback.format_exc()}")
        return error_msg, session_id or str(uuid.uuid4())

def ensure_markdown_format(text, template=""):
    """确保响应是有效的Markdown格式，并保持与模板一致的结构"""
    # 如果文本没有以标题开头，添加一个标题
    if not re.match(r"^#+\s", text):
        # 尝试从模板中提取标题
        title_match = re.search(r"^#+\s+(.+)$", template, re.MULTILINE)
        if title_match:
            title = title_match.group(1)
            text = f"# {title}\n\n" + text
        else:
            text = "# 试验方案\n\n" + text
    
    # 确保文档标识和签署页等关键部分
    if "文档标识" not in text and "文档标识" in template:
        # 从模板中提取文档标识部分
        doc_id_match = re.search(r"文档标识：(.+?)\n", template)
        if doc_id_match:
            doc_id = doc_id_match.group(1)
            text = f"文档标识：{doc_id}\n\n" + text
    
    # 确保有适当的章节结构
    sections = [
        "范围", "试验依据", "试验性质", "试验目的", "试验时间和地点",
        "试验对象范围", "试验环境与设备", "试验内容及方法", "试验中断与恢复",
        "试验结果评定", "试验组织及任务分工", "风险分析与相关措施"
    ]
    
    # 检查是否包含主要章节，如果没有则添加
    for section in sections:
        if not re.search(f"#+\\s+{section}", text):
            # 尝试在适当位置添加缺失的章节
            if section == "范围":
                text = re.sub(r"^#.+?\n\n", f"\\g<0>## {section}\n\n", text, count=1)
            else:
                # 找到上一个章节并在此之后添加新章节
                prev_index = sections.index(section) - 1
                if prev_index >= 0:
                    prev_section = sections[prev_index]
                    if re.search(f"#+\\s+{prev_section}", text):
                        text = re.sub(f"(#+\\s+{prev_section}.+?)(?=#+|$)", f"\\g<1>## {section}\n\n", text, flags=re.DOTALL)
    
    return text