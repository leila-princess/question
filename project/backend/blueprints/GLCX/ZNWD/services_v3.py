"""
[遗留] 智能问答 v3 - 仅 /api/znwd/chat/stream_v3。现网请用 /chat/stream_v4。见 FLOW.md
"""

import json
import logging
import os
import re
from datetime import datetime
from typing import Generator, List, Dict, Any, Tuple, Optional

from .database_prompts import CYPHER_GENERATION_PROMPT, NEO4J_SCHEMA
from utils.neo4j_flgl_status import flgl_status_cypher_param

logger = logging.getLogger(__name__)

DEBUG_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'logs', 'znwd')
os.makedirs(DEBUG_LOG_DIR, exist_ok=True)


def debug_log(data: Any, tag: str = ''):
    try:
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        fname = os.path.join(DEBUG_LOG_DIR, f"znwd_debug_{datetime.now().strftime('%Y%m%d')}.log")
        with open(fname, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*80}\n[{ts}] {tag}\n{'='*80}\n")
            if isinstance(data, str):
                f.write(data)
            else:
                f.write(json.dumps(data, ensure_ascii=False, indent=2, default=str))
            f.write('\n')
    except Exception:
        pass


SFL = " IN $flgl_enabled_status"  # 与 flgl_status_cypher_param 同参
QUERY_TEMPLATES = {
    "设备": {
        "基础": f"MATCH (e:Equipment) WHERE e.Status{SFL} AND e.Name CONTAINS $keyword RETURN e",
        "带类型": f"MATCH (et:EquipmentType)-[:HAS]->(e:Equipment) WHERE et.Status{SFL} AND e.Status{SFL} AND e.Name CONTAINS $keyword RETURN et, e",
        "带项目": f"MATCH (e:Equipment)-[:CON]->(p:TestProject) WHERE e.Status{SFL} AND p.Status{SFL} AND e.Name CONTAINS $keyword RETURN e, p",
        "全部": f"MATCH (e:Equipment) WHERE e.Status{SFL} AND e.Name CONTAINS $keyword OPTIONAL MATCH (et:EquipmentType)-[:HAS]->(e) WHERE (et) IS NULL OR (et).Status{SFL} OPTIONAL MATCH (e)-[:CON]->(p:TestProject) WHERE (p) IS NULL OR (p).Status{SFL} RETURN et, e, p"
    },
    "设备类型": {
        "基础": f"MATCH (et:EquipmentType) WHERE et.Status{SFL} AND (et.Name CONTAINS $keyword OR et.Type CONTAINS $keyword) RETURN et",
        "带设备": f"MATCH (et:EquipmentType)-[:HAS]->(e:Equipment) WHERE et.Status{SFL} AND e.Status{SFL} AND (et.Name CONTAINS $keyword OR et.Type CONTAINS $keyword) RETURN et, e"
    },
    "环境": {
        "基础": f"MATCH (env:Environment) WHERE env.Status{SFL} AND (env.Name CONTAINS $keyword OR env.Type CONTAINS $keyword) RETURN env",
        "带项目": f"MATCH (env:Environment)-[:CON]->(p:TestProject) WHERE env.Status{SFL} AND p.Status{SFL} AND env.Name CONTAINS $keyword RETURN env, p"
    },
    "项目": {
        "基础": f"MATCH (p:TestProject) WHERE p.Status{SFL} AND p.Name CONTAINS $keyword RETURN p",
        "带方法": f"MATCH (p:TestProject)-[:CON]->(m:TestMethod) WHERE p.Status{SFL} AND m.Status{SFL} AND p.Name CONTAINS $keyword RETURN p, m",
        "带设备": f"MATCH (e:Equipment)-[:CON]->(p:TestProject) WHERE e.Status{SFL} AND p.Status{SFL} AND p.Name CONTAINS $keyword RETURN e, p"
    },
    "指标": {
        "基础": f"MATCH (i:Indicator) WHERE i.Status{SFL} AND i.Name CONTAINS $keyword RETURN i",
        "带方法": f"MATCH (i:Indicator)-[:CON]->(m:TestMethod) WHERE i.Status{SFL} AND m.Status{SFL} AND i.Name CONTAINS $keyword RETURN i, m"
    },
    "方法": {
        "基础": f"MATCH (m:TestMethod) WHERE m.Status{SFL} AND m.Name CONTAINS $keyword RETURN m",
        "带文件": f"MATCH (m:TestMethod) WHERE m.Status{SFL} AND m.Name CONTAINS $keyword OPTIONAL MATCH (f:File)-[:BELONGS_TO]->(m) WHERE (f) IS NULL OR (f).Status{SFL} RETURN m, f",
        "带项目": f"MATCH (p:TestProject)-[:CON]->(m:TestMethod) WHERE p.Status{SFL} AND m.Status{SFL} AND m.Name CONTAINS $keyword RETURN p, m"
    },
    "文件": {
        "基础": f"MATCH (f:File) WHERE f.Status{SFL} AND f.Name CONTAINS $keyword RETURN f",
        "带方法": f"MATCH (f:File)-[:BELONGS_TO]->(m:TestMethod) WHERE f.Status{SFL} AND m.Status{SFL} AND f.Name CONTAINS $keyword RETURN f, m"
    }
}

RELATED_QUERY_TEMPLATES = {
    "设备": f"MATCH (e:Equipment) WHERE e.Status{SFL} RETURN e LIMIT 5",
    "设备类型": f"MATCH (et:EquipmentType) WHERE et.Status{SFL} RETURN et LIMIT 5",
    "环境": f"MATCH (env:Environment) WHERE env.Status{SFL} RETURN env LIMIT 5",
    "项目": f"MATCH (p:TestProject) WHERE p.Status{SFL} RETURN p LIMIT 5",
    "指标": f"MATCH (i:Indicator) WHERE i.Status{SFL} RETURN i LIMIT 5",
    "方法": f"MATCH (m:TestMethod) WHERE m.Status{SFL} RETURN m LIMIT 5",
    "文件": f"MATCH (f:File) WHERE f.Status{SFL} RETURN f LIMIT 5"
}
del SFL


def neo4j_query(driver, cypher: str, params: dict = None) -> List[Dict]:
    if not driver:
        return []
    try:
        merged = {**(params or {}), **flgl_status_cypher_param()}
        with driver.session() as session:
            result = session.run(cypher, merged)
            nodes = []
            for record in result:
                for key in record.keys():
                    value = record[key]
                    if value and hasattr(value, 'items'):
                        nodes.append({
                            'labels': list(value.labels),
                            'properties': {k: v for k, v in value.items() if v is not None}
                        })
            return nodes
    except Exception as e:
        logger.error(f"Neo4j查询失败: {e}")
        return []


def chroma_query(collection, query: str, top_k: int = 5) -> List[Dict]:
    if not collection:
        return []
    try:
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
        ef = ONNXMiniLM_L6_V2()
        query_embedding = ef([query])[0]
        
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k
        )
        
        chunks = []
        if results and results.get('documents') and results['documents'][0]:
            for i, doc in enumerate(results['documents'][0]):
                metadata = results['metadatas'][0][i] if results.get('metadatas') and results['metadatas'][0] else {}
                chunks.append({
                    'content': doc,
                    'doc_id': metadata.get('doc_id', ''),
                    'title': metadata.get('title', ''),
                    'type': metadata.get('type', '')
                })
        return chunks
    except Exception as e:
        logger.error(f"Chroma查询失败: {e}")
        return []


# def parse_query_intent(llm_response: str) -> Dict:
#     intent = {
#         'query_types': [],
#         'keywords': [],
#         'relations': '',
#         'vector_keywords': ''
#     }
    
#     query_intent_match = re.search(r'\[QUERY_INTENT\](.*?)\[/QUERY_INTENT\]', llm_response, re.DOTALL)
#     if query_intent_match:
#         content = query_intent_match.group(1)
        
#         types_match = re.search(r'查询类型[:：]\s*(.+)', content)
#         if types_match:
#             types_str = types_match.group(1).strip()
#             intent['query_types'] = [t.strip() for t in re.split(r'[,，、]', types_str) if t.strip()]
        
#         keywords_match = re.search(r'查询关键词[:：]\s*(.+)', content)
#         if keywords_match:
#             keywords_str = keywords_match.group(1).strip()
#             intent['keywords'] = [k.strip() for k in re.split(r'[,，、]', keywords_str) if k.strip()]
        
#         relations_match = re.search(r'关联查询[:：]\s*(.+)', content)
#         if relations_match:
#             intent['relations'] = relations_match.group(1).strip()
    
#     vector_query_match = re.search(r'\[VECTOR_QUERY\](.*?)\[/VECTOR_QUERY\]', llm_response, re.DOTALL)
#     if vector_query_match:
#         content = vector_query_match.group(1)
#         keywords_match = re.search(r'搜索关键词[:：]\s*(.+)', content)
#         if keywords_match:
#             intent['vector_keywords'] = keywords_match.group(1).strip()
    
#     return intent


# def execute_queries_by_intent(driver, collection, intent: Dict) -> Dict:
#     results = {
#         'neo4j': [],
#         'chroma': [],
#         'has_data': False
#     }
    
#     for query_type in intent['query_types']:
#         if query_type not in QUERY_TEMPLATES:
#             continue
        
#         templates = QUERY_TEMPLATES[query_type]
        
#         for keyword in intent['keywords']:
#             for template_name, cypher in templates.items():
#                 try:
#                     nodes = neo4j_query(driver, cypher, {'keyword': keyword})
#                     if nodes:
#                         for node in nodes:
#                             node['query_type'] = query_type
#                             node['match_keyword'] = keyword
#                         results['neo4j'].extend(nodes)
#                         results['has_data'] = True
#                 except Exception as e:
#                     logger.error(f"查询失败 {template_name}: {e}")
    
#     if intent['vector_keywords']:
#         chunks = chroma_query(collection, intent['vector_keywords'])
#         if chunks:
#             results['chroma'].extend(chunks)
#             results['has_data'] = True
    
#     if not results['has_data'] and intent['query_types']:
#         for query_type in intent['query_types']:
#             if query_type in RELATED_QUERY_TEMPLATES:
#                 try:
#                     nodes = neo4j_query(driver, RELATED_QUERY_TEMPLATES[query_type])
#                     if nodes:
#                         for node in nodes:
#                             node['query_type'] = query_type
#                             node['match_keyword'] = '相关数据'
#                         results['neo4j'].extend(nodes)
#                 except Exception as e:
#                     logger.error(f"相关查询失败: {e}")
    
#     return results


def parse_cypher_response(llm_response: str) -> Tuple[str, str]:
    """解析LLM返回的Cypher和向量关键词"""
    cypher = ""
    vector_keywords = ""
    
    cypher_match = re.search(r'\[CYPHER\](.*?)\[/CYPHER\]', llm_response, re.DOTALL)
    if cypher_match:
        cypher = cypher_match.group(1).strip()
    
    vector_match = re.search(r'\[VECTOR_QUERY\](.*?)\[/VECTOR_QUERY\]', llm_response, re.DOTALL)
    if vector_match:
        vector_keywords = vector_match.group(1).strip()
    
    return cypher, vector_keywords


def execute_cypher_safely(driver, cypher: str) -> List[Dict]:
    """安全执行Cypher查询，只允许读操作"""
    if not driver or not cypher or '[NO_CYPHER]' in cypher:
        return []
    
    # 安全检查：只允许读操作
    cypher_upper = cypher.upper()
    write_keywords = ['CREATE', 'DELETE', 'SET', 'REMOVE', 'MERGE', 'DROP']
    if any(wk in cypher_upper for wk in write_keywords):
        logger.warning(f"拒绝写操作: {cypher}")
        return []
    
    try:
        with driver.session() as session:
            result = session.run(cypher)
            nodes = []
            for record in result:
                for key in record.keys():
                    value = record[key]
                    if value and hasattr(value, 'items'):  # Node对象
                        nodes.append({
                            'labels': list(value.labels),
                            'properties': {k: v for k, v in value.items() if v is not None}
                        })
                    elif isinstance(value, list):
                        for item in value:
                            if hasattr(item, 'items'):
                                nodes.append({
                                    'labels': list(item.labels),
                                    'properties': {k: v for k, v in item.items() if v is not None}
                                })
            return nodes
    except Exception as e:
        logger.error(f"Cypher执行失败: {e}\nCypher: {cypher}")
        return []


def execute_with_retry(client, model, question, driver, collection, max_retries=2):
    """带重试的查询执行"""
    current_question = question
    for attempt in range(max_retries):
        # 生成Cypher和向量关键词
        prompt = CYPHER_GENERATION_PROMPT.format(schema=NEO4J_SCHEMA)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是Neo4j查询专家，只输出指定格式。"},
                {"role": "user", "content": f"{prompt}\n\n用户问题：{current_question}"}
            ],
            temperature=0.2,
        )
        llm_output = response.choices[0].message.content
        cypher, vector_keywords = parse_cypher_response(llm_output)
        
        # 执行Cypher
        neo4j_results = execute_cypher_safely(driver, cypher)
        
        # 如果成功或最后一次尝试，跳出
        if neo4j_results or attempt == max_retries - 1:
            break
        else:
            # 重试时提示无结果
            current_question = f"上次查询没有结果，请重新生成Cypher：{question}"
    
    # 向量检索
    chroma_results = chroma_query(collection, vector_keywords) if vector_keywords else []
    
    return neo4j_results, chroma_results


def format_results_for_llm(results: Dict) -> str:
    formatted = []
    
    if results['neo4j']:
        formatted.append("### 图数据库查询结果")
        for i, node in enumerate(results['neo4j'][:20], 1):
            labels = ', '.join(node.get('labels', ['Unknown']))
            props = node.get('properties', {})
            name = props.get('Name') or props.get('name') or '未命名'
            query_type = node.get('query_type', '')
            formatted.append(f"{i}. [{labels}] {name} (类型: {query_type})")
            for key, value in props.items():
                if key not in ('Id', 'Name', 'name', 'Status') and value:
                    formatted.append(f"   - {key}: {value}")
    
    if results['chroma']:
        formatted.append("\n### 知识库查询结果")
        for i, chunk in enumerate(results['chroma'][:10], 1):
            title = chunk.get('title', '未知文档')
            content = chunk.get('content', '')[:300]
            formatted.append(f"{i}. [{title}]")
            formatted.append(f"   {content}...")
    
    if not results['neo4j'] and not results['chroma']:
        formatted.append("未找到直接相关数据。")
        formatted.append("建议：尝试使用更通用的关键词，或查询相关概念。")
    
    return '\n'.join(formatted)


ANSWER_PROMPT_TEMPLATE = """## 查询结果

{query_results}

## 用户问题
{user_question}

## 回答要求

基于以上查询结果回答用户问题：

1. **必须基于数据**：只能使用上述查询结果中的数据，绝对不能编造
2. **准确引用**：使用数据中的具体名称（如设备名、标准名）
3. **相关概念**：如果直接数据不足，可以提供数据库中的相关概念
4. **诚实说明**：如果确实没有相关信息，明确说明"数据库中暂无此信息"
5. **中文表达**：所有节点类型必须转换为中文，不要出现英文节点名称

节点类型中文对照：
- EquipmentType → 设备类型
- Equipment → 设备
- Environment → 环境
- TestProject → 项目
- Indicator → 指标
- TestMethod → 测试方法
- File → 文件

请输出：
[THINK]
[分析查询结果，说明哪些数据可以用于回答]
[/THINK]

[CONTENT]
[基于数据的正式回答，使用中文，节点名称必须转为中文]
[/CONTENT]
"""


def get_answer_prompt(user_question: str, query_results: str) -> str:
    return ANSWER_PROMPT_TEMPLATE.format(
        query_results=query_results,
        user_question=user_question
    )


# def llm_stream_with_queries(
#     client, 
#     model: str, 
#     question: str, 
#     driver, 
#     collection,
#     session_id: str = '',
#     memory_summary: str = ''
# ) -> Generator[Tuple[str, str], None, None]:
#     if not client:
#         yield ("error", "LLM客户端未初始化")
#         return
    
#     from .database_prompts import get_main_prompt, get_system_prompt, MEMORY_CONTEXT_TEMPLATE
    
#     debug_log(question, "用户问题")
    
#     memory_context = ''
#     if memory_summary:
#         memory_context = MEMORY_CONTEXT_TEMPLATE.format(summary=memory_summary)
    
#     yield ("step", {"type": "intent", "message": "正在分析问题并生成查询意图..."})
    
#     prompt = get_main_prompt(question, memory_context)
#     system_prompt = get_system_prompt()
    
#     try:
#         yield ("thinking_start", "")
#         yield ("thinking", "## 第一步：分析用户问题并生成查询策略\n\n")
        
#         first_llm_response = ""
#         logger.info(f"Starting first LLM call for intent analysis")
#         stream1 = client.chat.completions.create(
#             model=model,
#             messages=[
#                 {"role": "system", "content": system_prompt},
#                 {"role": "user", "content": prompt}
#             ],
#             temperature=0.3,
#             stream=True
#         )
        
#         chunk_count = 0
#         for chunk in stream1:
#             if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
#                 token = chunk.choices[0].delta.content
#                 first_llm_response += token
#                 chunk_count += 1
#                 if chunk_count % 10 == 0:
#                     logger.debug(f"Streamed {chunk_count} chunks, current response length: {len(first_llm_response)}")
#                 yield ("thinking", token)
        
#         logger.info(f"First LLM call completed, received {chunk_count} chunks")
        
#         debug_log(first_llm_response, "LLM查询意图响应")
        
#         intent = parse_query_intent(first_llm_response)
#         debug_log(intent, "解析后的查询意图")
        
#         yield ("thinking", f"\n\n## 第二步：执行数据库查询\n\n")
#         yield ("thinking", f"- 查询类型: {', '.join(intent['query_types'])}\n")
#         yield ("thinking", f"- 查询关键词: {', '.join(intent['keywords'])}\n")
#         if intent['vector_keywords']:
#             yield ("thinking", f"- 向量搜索关键词: {intent['vector_keywords']}\n")
        
#         results = execute_queries_by_intent(driver, collection, intent)
#         debug_log(results, "数据库查询结果")
        
#         neo4j_count = len(results['neo4j'])
#         chroma_count = len(results['chroma'])
#         yield ("thinking", f"- 图数据库查询结果: {neo4j_count} 条\n")
#         yield ("thinking", f"- 知识库查询结果: {chroma_count} 条\n")
        
#         formatted_results = format_results_for_llm(results)
#         yield ("thinking", "\n## 第三步：基于查询结果生成回答\n\n")
        
#         answer_prompt = get_answer_prompt(question, formatted_results)
        
#         logger.info(f"Starting second LLM call for answer generation")
#         stream = client.chat.completions.create(
#             model=model,
#             messages=[
#                 {"role": "system", "content": system_prompt},
#                 {"role": "user", "content": answer_prompt}
#             ],
#             temperature=0.3,
#             stream=True
#         )
        
#         full_answer = ''
#         in_think = False
#         in_content = False
#         buffer = ''
#         TAG_TAIL = 12
#         chunk_count = 0
        
#         for chunk in stream:
#             if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
#                 token = chunk.choices[0].delta.content
#                 buffer += token
#                 chunk_count += 1
                
#                 while True:
#                     if not in_think and not in_content:
#                         if "[THINK]" in buffer:
#                             parts = buffer.split("[THINK]", 1)
#                             buffer = parts[1] if len(parts) > 1 else ""
#                             in_think = True
#                             continue
#                         elif "[CONTENT]" in buffer:
#                             parts = buffer.split("[CONTENT]", 1)
#                             buffer = parts[1] if len(parts) > 1 else ""
#                             in_content = True
#                             yield ("answer_start", {})
#                             continue
#                         else:
#                             if len(buffer) > 50:
#                                 in_think = True
#                                 continue
#                     elif in_think:
#                         if "[/THINK]" in buffer:
#                             parts = buffer.split("[/THINK]", 1)
#                             think_text = parts[0]
#                             buffer = parts[1] if len(parts) > 1 else ""
#                             if think_text.strip():
#                                 yield ("thinking", think_text)
#                             in_think = False
#                             if "[CONTENT]" in buffer:
#                                 parts = buffer.split("[CONTENT]", 1)
#                                 buffer = parts[1] if len(parts) > 1 else ""
#                                 in_content = True
#                                 yield ("answer_start", {})
#                                 continue
#                         else:
#                             safe = max(0, len(buffer) - TAG_TAIL)
#                             if safe > 0:
#                                 to_send = buffer[:safe]
#                                 buffer = buffer[safe:]
#                                 if to_send.strip():
#                                     yield ("thinking", to_send)
#                     elif in_content:
#                         if "[/CONTENT]" in buffer:
#                             parts = buffer.split("[/CONTENT]", 1)
#                             content_text = parts[0]
#                             buffer = parts[1] if len(parts) > 1 else ""
#                             if content_text.strip():
#                                 full_answer += content_text
#                                 yield ("answer", content_text)
#                             in_content = False
#                         else:
#                             safe = max(0, len(buffer) - TAG_TAIL)
#                             if safe > 0:
#                                 to_send = buffer[:safe]
#                                 buffer = buffer[safe:]
#                                 full_answer += to_send
#                                 yield ("answer", to_send)
#                     break
        
#         if in_think and buffer.strip():
#             yield ("thinking", buffer)
#         if in_content and buffer.strip():
#             full_answer += buffer
#             yield ("answer", buffer)
        
#         logger.info(f"Second LLM call completed, received {chunk_count} chunks, answer length: {len(full_answer)}")
#         yield ("done", {"fullAnswer": full_answer})
        
#     except Exception as e:
#         err_str = str(e)
#         logger.error(f"LLM调用失败: {e}")
#         if '401' in err_str or 'invalid_api_key' in err_str:
#             yield ("error", "API Key错误，请检查配置")
#         else:
#             yield ("error", f"生成出错：{err_str}")

def llm_stream_with_queries(
    client, 
    model: str, 
    question: str, 
    driver, 
    collection,
    session_id: str = '',
    memory_summary: str = ''
) -> Generator[Tuple[str, str], None, None]:
    """
    流式 RAG 对话 - 使用 LLM 生成 Cypher 查询（替代意图解析 + 模板）
    """
    if not client:
        yield ("error", "LLM客户端未初始化")
        return

    # 导入 prompt 模板（请确保已定义）
    from .database_prompts import (
        CYPHER_GENERATION_PROMPT,
        NEO4J_SCHEMA,
        MEMORY_CONTEXT_TEMPLATE,
        # ANSWER_PROMPT_TEMPLATE  # 复用原有的答案生成模板
    )
    from .services_v3 import (
        execute_cypher_safely,
        parse_cypher_response,
        chroma_query,
        format_results_for_llm
    )

    debug_log(question, "用户问题")

    # 处理记忆上下文
    memory_context = ''
    if memory_summary:
        memory_context = MEMORY_CONTEXT_TEMPLATE.format(summary=memory_summary)

    yield ("step", {"type": "intent", "message": "正在分析问题并生成查询语句..."})
    yield ("thinking_start", "")
    yield ("thinking", "## 第一步：生成 Cypher 查询语句\n\n")

    # 构造生成 Cypher 的 prompt
    cypher_prompt = CYPHER_GENERATION_PROMPT.format(schema=NEO4J_SCHEMA)
    if memory_context:
        cypher_prompt = memory_context + "\n\n" + cypher_prompt

    # 调用 LLM 生成 Cypher（非流式，避免解析混乱）
    try:
        first_response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是 Neo4j 查询专家，只输出指定格式的 Cypher 和向量关键词。"},
                {"role": "user", "content": f"{cypher_prompt}\n\n用户问题：{question}"}
            ],
            temperature=0.2,
            stream=False
        )
        llm_output = first_response.choices[0].message.content
        debug_log(llm_output, "LLM 生成的原始内容")

        # 解析出 Cypher 和向量关键词
        cypher, vector_keywords = parse_cypher_response(llm_output)

        # 流式输出解析结果（供前端展示）
        if cypher and "[NO_CYPHER]" not in cypher:
            yield ("thinking", f"**生成的 Cypher 查询：**\n```cypher\n{cypher}\n```\n\n")
        else:
            yield ("thinking", "未能生成有效的 Cypher 查询，将仅使用向量检索。\n\n")

        if vector_keywords:
            yield ("thinking", f"**向量检索关键词：** {vector_keywords}\n\n")

    except Exception as e:
        logger.error(f"生成 Cypher 失败: {e}")
        yield ("thinking", f"生成查询语句失败：{str(e)}，将尝试降级检索。\n\n")
        cypher = ""
        vector_keywords = ""

    # 第二步：执行查询
    yield ("thinking", "## 第二步：执行数据库查询\n\n")
    neo4j_results = []
    chroma_results = []

    # 执行 Cypher 查询（如果存在）
    if cypher and "[NO_CYPHER]" not in cypher:
        yield ("thinking", "正在执行图数据库查询...\n")
        neo4j_results = execute_cypher_safely(driver, cypher)
        yield ("thinking", f"图数据库返回 {len(neo4j_results)} 条记录。\n")

    # 执行向量检索（如果有关键词）
    if vector_keywords:
        yield ("thinking", "正在执行知识库语义检索...\n")
        chroma_results = chroma_query(collection, vector_keywords, top_k=5)
        yield ("thinking", f"知识库返回 {len(chroma_results)} 条相关片段。\n")

    # 如果两种方式都没有结果，尝试降级：用原始问题做向量检索
    if not neo4j_results and not chroma_results:
        yield ("thinking", "未找到直接相关数据，尝试使用原始问题检索知识库...\n")
        chroma_results = chroma_query(collection, question, top_k=5)
        yield ("thinking", f"知识库返回 {len(chroma_results)} 条相关片段。\n")

    # 第三步：基于结果生成最终答案
    yield ("thinking", "\n## 第三步：生成最终答案\n\n")

    # 格式化查询结果供 LLM 使用
    results_dict = {
        'neo4j': neo4j_results,
        'chroma': chroma_results,
        'has_data': bool(neo4j_results or chroma_results)
    }
    formatted_results = format_results_for_llm(results_dict)

    # 使用原有的答案生成 prompt
    answer_prompt = ANSWER_PROMPT_TEMPLATE.format(
        query_results=formatted_results,
        user_question=question
    )

    try:
        # 流式生成答案
        stream = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是智能问答助手，必须基于提供的查询结果回答问题，不能编造数据。"},
                {"role": "user", "content": answer_prompt}
            ],
            temperature=0.3,
            stream=True
        )

        full_answer = ''
        in_think = False
        in_content = False
        buffer = ''
        TAG_TAIL = 12

        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                token = chunk.choices[0].delta.content
                buffer += token

                # 解析 [THINK] 和 [CONTENT] 标签（复用原有逻辑）
                while True:
                    if not in_think and not in_content:
                        if "[THINK]" in buffer:
                            parts = buffer.split("[THINK]", 1)
                            buffer = parts[1] if len(parts) > 1 else ""
                            in_think = True
                            continue
                        elif "[CONTENT]" in buffer:
                            parts = buffer.split("[CONTENT]", 1)
                            buffer = parts[1] if len(parts) > 1 else ""
                            in_content = True
                            yield ("answer_start", {})
                            continue
                        else:
                            # 防止缓冲过大
                            if len(buffer) > 50:
                                in_think = True
                                continue
                    elif in_think:
                        if "[/THINK]" in buffer:
                            parts = buffer.split("[/THINK]", 1)
                            think_text = parts[0]
                            buffer = parts[1] if len(parts) > 1 else ""
                            if think_text.strip():
                                yield ("thinking", think_text)
                            in_think = False
                            if "[CONTENT]" in buffer:
                                parts = buffer.split("[CONTENT]", 1)
                                buffer = parts[1] if len(parts) > 1 else ""
                                in_content = True
                                yield ("answer_start", {})
                                continue
                        else:
                            safe = max(0, len(buffer) - TAG_TAIL)
                            if safe > 0:
                                to_send = buffer[:safe]
                                buffer = buffer[safe:]
                                if to_send.strip():
                                    yield ("thinking", to_send)
                    elif in_content:
                        if "[/CONTENT]" in buffer:
                            parts = buffer.split("[/CONTENT]", 1)
                            content_text = parts[0]
                            buffer = parts[1] if len(parts) > 1 else ""
                            if content_text.strip():
                                full_answer += content_text
                                yield ("answer", content_text)
                            in_content = False
                        else:
                            safe = max(0, len(buffer) - TAG_TAIL)
                            if safe > 0:
                                to_send = buffer[:safe]
                                buffer = buffer[safe:]
                                full_answer += to_send
                                yield ("answer", to_send)
                    break

        # 处理剩余缓冲
        if in_think and buffer.strip():
            yield ("thinking", buffer)
        if in_content and buffer.strip():
            full_answer += buffer
            yield ("answer", buffer)

        yield ("done", {"fullAnswer": full_answer})

    except Exception as e:
        logger.error(f"生成最终答案失败: {e}")
        yield ("error", f"生成答案出错：{str(e)}")


def generate_conversation_summary(client, model: str, question: str, answer: str) -> str:
    from .database_prompts import SUMMARY_PROMPT
    
    if not client:
        return ''
    
    try:
        prompt = SUMMARY_PROMPT.format(question=question, answer=answer)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        summary = response.choices[0].message.content or ''
        return summary.strip()
    except Exception as e:
        logger.error(f"生成对话摘要失败: {e}")
        return ''


