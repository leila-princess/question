import json
import logging
import os
import re
import math
from datetime import datetime
from typing import Generator, List, Dict, Any, Tuple

import numpy as np

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


def split_keywords(text: str) -> List[str]:
    text = text.strip()
    if not text:
        return []
    cleaned = re.sub(r'[?？!！。，,、;；:：\s]+', ' ', text).strip()
    if not cleaned:
        return []
    keywords = []
    try:
        import jieba
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            words = jieba.lcut(cleaned)
        keywords = [w.strip() for w in words if w.strip() and len(w.strip()) >= 2]
    except Exception:
        pass
    if not keywords:
        keywords = [p.strip() for p in re.split(r'[\s,，、;；]+', cleaned) if p.strip()]
    if len(cleaned) >= 2 and cleaned not in keywords:
        keywords.append(cleaned)
    seen = set()
    result = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            result.append(kw)
    debug_log(result, f"分词结果: {text}")
    return result


def classify_intent(query: str) -> Tuple[str, int]:
    """
    查询意图分类：broad(宽泛) vs specific(具体)
    返回 (intent_type, top_k)
    broad → 需要更多上下文 (top_k=8)
    specific → 精确匹配即可 (top_k=3)
    """
    broad_keywords = ['什么是', '介绍', '概述', '总结', '有哪些', '如何', '怎么', '为什么', '区别', '比较']
    specific_keywords = ['标准', '要求', '参数', '数值', '范围', '方法', '步骤', '条件']

    is_broad = any(kw in query for kw in broad_keywords)
    is_specific = any(kw in query for kw in specific_keywords)

    if is_broad and not is_specific:
        return 'broad', 8
    elif is_specific and not is_broad:
        return 'specific', 3
    else:
        return 'normal', 5


def rerank_cross_encoder(query: str, chunks: List[Dict], top_k: int = 5) -> List[Dict]:
    """
    使用 cross-encoder 对候选块重新排序
    模型: cross-encoder/ms-marco-MiniLM-L-6-v2
    """
    if not chunks:
        return []

    try:
        from sentence_transformers import CrossEncoder
        from backend.config import Config
        model_path = Config.LOCAL_CROSSENCODER_MODEL_PATH
        if model_path and os.path.exists(model_path):
            model = CrossEncoder(model_path)
        else:
            model = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
        pairs = [[query, chunk.get('content', '')] for chunk in chunks]
        scores = model.predict(pairs, show_progress_bar=False)

        for i, chunk in enumerate(chunks):
            chunk['rerank_score'] = float(scores[i])

        chunks.sort(key=lambda x: x.get('rerank_score', 0), reverse=True)
        debug_log(
            [{'source': c.get('source', ''), 'score': round(c.get('rerank_score', 0), 4)} for c in chunks],
            "Cross-encoder 重排序结果"
        )
        return chunks[:top_k]

    except Exception as e:
        logger.warning(f"Cross-encoder 重排序失败: {e}，使用原始排序")
        return chunks[:top_k]


def mmr_select(chunks: List[Dict], query_embedding: List[float], top_k: int, lambda_param: float = 0.6) -> List[Dict]:
    """
    MMR (Maximal Marginal Relevance) 选择
    同时考虑与查询的相关性和与已选块之间的差异性
    lambda_param: 0.0=纯多样性, 1.0=纯相关性
    """
    if not chunks:
        return []

    try:
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
        ef = ONNXMiniLM_L6_V2()
        content_embeddings = ef([c.get('content', '') for c in chunks])
    except Exception:
        return chunks[:top_k]

    selected = []
    remaining = list(range(len(chunks)))

    query_vec = np.array(query_embedding)
    emb_matrix = np.array(content_embeddings)

    while len(selected) < top_k and remaining:
        best_idx = -1
        best_score = -float('inf')

        for idx in remaining:
            relevance = np.dot(query_vec, emb_matrix[idx]) / (
                np.linalg.norm(query_vec) * np.linalg.norm(emb_matrix[idx]) + 1e-8
            )

            max_similarity = 0
            for sel_idx in selected:
                sim = np.dot(emb_matrix[sel_idx], emb_matrix[idx]) / (
                    np.linalg.norm(emb_matrix[sel_idx]) * np.linalg.norm(emb_matrix[idx]) + 1e-8
                )
                max_similarity = max(max_similarity, sim)

            mmr_score = lambda_param * relevance - (1 - lambda_param) * max_similarity
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = idx

        if best_idx >= 0:
            selected.append(best_idx)
            remaining.remove(best_idx)

    return [chunks[i] for i in selected]


def neo4j_fulltext_search(driver, keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
    if not driver:
        logger.warning("Neo4j driver 未初始化")
        return []

    keywords = split_keywords(keyword)
    if not keywords:
        return []

    try:
        with driver.session() as session:
            try:
                result = session.run(
                    """
                    CALL db.index.fulltext.queryNodes('all_nodes_index', $keyword)
                    YIELD node, score
                    RETURN node, score
                    LIMIT $limit
                    """,
                    keyword=' '.join(keywords),
                    limit=limit
                )
                nodes = []
                for record in result:
                    node = record['node']
                    nodes.append({
                        'labels': list(node.labels),
                        'properties': dict(node),
                        'score': float(record['score'])
                    })
                if nodes:
                    debug_log(nodes, "Neo4j 全文索引结果")
                    logger.info(f"Neo4j 全文索引命中 {len(nodes)} 个节点")
                    return nodes
            except Exception as e:
                logger.warning(f"全文索引查询失败，降级为 CONTAINS 查询: {e}")

            string_props = [
                'Name', 'name', 'Title', 'title', 'Abstract', 'abstract',
                'Label', 'label', 'description', 'content', 'Id',
                'ICS', 'CCS', 'Type', 'File_type', 'Status',
            ]
            conditions = []
            params = {'limit': limit}
            for i, kw in enumerate(keywords):
                key = f'kw{i}'
                prop_checks = [f"n.{prop} CONTAINS ${key}" for prop in string_props]
                prop_checks.append(f"ANY(item IN COALESCE(n.Keywords, []) WHERE item CONTAINS ${key})")
                prop_checks.append(f"ANY(item IN COALESCE(n.keywords, []) WHERE item CONTAINS ${key})")
                conditions.append(f"({' OR '.join(prop_checks)})")
                params[key] = kw

            where_clause = ' OR '.join(conditions)
            cypher = f"""
                MATCH (n)
                WHERE {where_clause}
                RETURN n, labels(n) as nodeLabels
                LIMIT $limit
            """

            debug_log({'cypher': cypher, 'params': params}, "Neo4j CONTAINS 查询语句")
            result = session.run(cypher, **params)
            nodes = []
            seen = set()
            for record in result:
                node = record['n']
                node_id = node.id
                if node_id in seen:
                    continue
                seen.add(node_id)
                nodes.append({
                    'labels': list(record['nodeLabels']),
                    'properties': dict(node),
                    'score': 1.0
                })

            debug_log(nodes, "Neo4j CONTAINS 查询结果")
            logger.info(f"Neo4j CONTAINS 查询命中 {len(nodes)} 个节点 (关键词: {keywords})")
            return nodes

    except Exception as e:
        logger.error(f"Neo4j 检索失败: {e}")
        return []


def chroma_semantic_search(collection, query: str) -> tuple:
    """
    检索 → cross-encoder 重排序 → MMR 去重 → 按 doc_id 聚合完整标准
    返回 (matched_chunks, full_standards)
    """
    if not collection:
        logger.warning("Chroma collection 未初始化")
        return [], []

    intent_type, base_top_k = classify_intent(query)
    debug_log({'query': query, 'intent': intent_type, 'base_top_k': base_top_k}, "查询意图分类")

    initial_k = base_top_k * 3

    try:
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
        ef = ONNXMiniLM_L6_V2()
        query_embedding = ef([query])[0]
    except Exception as e:
        logger.warning(f"获取 query embedding 失败: {e}")
        return [], []

    try:
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=initial_k
        )

        matched_chunks = []
        if results and results.get('documents') and results['documents'][0]:
            for i, doc in enumerate(results['documents'][0]):
                metadata = results['metadatas'][0][i] if results.get('metadatas') and results['metadatas'][0] else {}
                distance = results['distances'][0][i] if results.get('distances') and results['distances'][0] else None
                matched_chunks.append({
                    'content': doc,
                    'metadata': metadata,
                    'source': metadata.get('source', metadata.get('title', '未知来源')),
                    'distance': distance,
                    'doc_id': metadata.get('doc_id', ''),
                    'original_score': 1.0 - (distance or 0),
                })

        debug_log(matched_chunks, "Chroma 初始检索结果")

        # Cross-encoder 重排序
        matched_chunks = rerank_cross_encoder(query, matched_chunks, top_k=initial_k)

        # MMR 去重选择
        matched_chunks = mmr_select(matched_chunks, query_embedding, top_k=base_top_k)

        debug_log(matched_chunks, "Chroma MMR 去重后结果")

        # 按 doc_id 聚合完整标准
        doc_ids = set(c.get('doc_id', '') for c in matched_chunks if c.get('doc_id'))
        full_standards = []
        for doc_id in doc_ids:
            try:
                all_docs = collection.get(
                    where={'doc_id': doc_id},
                    include=['documents', 'metadatas']
                )
                if all_docs and all_docs['documents']:
                    full_text = '\n\n'.join(all_docs['documents'])
                    meta = all_docs['metadatas'][0] if all_docs['metadatas'] else {}
                    full_standards.append({
                        'doc_id': doc_id,
                        'title': meta.get('title', doc_id),
                        'content': full_text,
                        'chunk_count': len(all_docs['documents']),
                    })
            except Exception as e:
                logger.warning(f"获取标准 {doc_id} 完整内容失败: {e}")

        debug_log(
            [{'doc_id': s['doc_id'], 'title': s['title'], 'chunk_count': s['chunk_count'], 'content_len': len(s['content'])} for s in full_standards],
            "Chroma 完整标准内容"
        )

        logger.info(f"Chroma 检索: intent={intent_type}, 初始{len(matched_chunks)}→重排序→MMR→{len(matched_chunks)}片段, {len(full_standards)}个标准")
        return matched_chunks, full_standards

    except Exception as e:
        logger.error(f"Chroma 检索失败: {e}")
        return [], []


def build_context(neo4j_results: List[Dict], full_standards: List[Dict]) -> tuple:
    neo4j_parts = []
    for i, node in enumerate(neo4j_results):
        labels = ', '.join(node.get('labels', ['Unknown']))
        props = node.get('properties', {})
        prop_str = ', '.join(f'{k}={v}' for k, v in props.items() if k != 'id')
        neo4j_parts.append(f"[节点{i+1}] 类型: {labels} | {prop_str}")

    neo4j_context = '\n'.join(neo4j_parts) if neo4j_parts else '图数据库中未找到相关信息。'

    standard_parts = []
    for i, std in enumerate(full_standards):
        standard_parts.append(f"【标准 {std['doc_id']}】{std['title']}\n{std['content']}")

    chroma_context = '\n\n'.join(standard_parts) if standard_parts else '知识库中未找到相关文档。'

    debug_log(neo4j_context, "LLM 输入 - Neo4j 上下文")
    debug_log(chroma_context[:2000] + ('...' if len(chroma_context) > 2000 else ''), "LLM 输入 - Chroma 上下文（前2000字）")

    return neo4j_context, chroma_context


PROMPT_TEMPLATE = """你是一个智能助手。请基于以下两部分信息回答问题：

【图数据库信息】
{neo4j_context}

【文档知识库信息】
{chroma_context}

问题：{question}

要求：如果信息不足，请明确说"根据现有信息无法回答"。不要编造信息。请用中文回答，回答要准确、简洁、有条理。"""


def llm_stream_response(client, model: str, question: str, neo4j_context: str, chroma_context: str) -> Generator[str, None, None]:
    if not client:
        yield "【错误】LLM 客户端未初始化"
        return

    prompt = PROMPT_TEMPLATE.format(
        neo4j_context=neo4j_context,
        chroma_context=chroma_context,
        question=question
    )

    debug_log(prompt, "LLM 完整 Prompt 输入")

    full_response = ''
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            stream=True
        )

        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                token = chunk.choices[0].delta.content
                full_response += token
                yield token

        debug_log(full_response, "LLM 完整输出")

    except Exception as e:
        err_str = str(e)
        logger.error(f"LLM 流式调用失败: {e}")
        debug_log(err_str, "LLM 调用错误")
        if '401' in err_str or 'invalid_api_key' in err_str:
            yield f"\n\n**API Key 错误**：请检查 `backend/config.py` 中的 `OPENAI_API_KEY` 是否正确。"
        else:
            yield f"\n\n【生成出错】{err_str}"
