"""
ZNWD LLM 意图分类（轻量，仅 intent 枚举）。

现网 Preflight/V5 请用 znwd_llm_router.route_question（ZNWD_INTENT_MODE=llm），
含业务模板与关键词查库。本模块的 classify_intent 供兼容或回退。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .intent_classifier import Intent, IntentResult, classify_intent as rule_classify_intent
from .znwd_intent_mode import is_llm_routing_enabled

logger = logging.getLogger(__name__)

# LLM 意图分类提示词
LLM_INTENT_CLASSIFY_PROMPT = """你是一个专业的意图分类器。请分析用户的问题，将其分类到以下意图之一：

## 意图类型说明

1. **ENUMERATION**（枚举类）：询问某类节点有哪些，如"有哪些设备类型"、"列出所有环境"、"显示全部测试项目"
2. **RELATION_QUERY**（关系查询）：询问某个对象关联的项目/指标/方法，如"无人机有哪些试验项目"、"高温工作性能测试的方法是什么"、"XX的指标有哪些"
3. **STANDARD_CHAPTER**（标准章节）：询问标准/文档的试验过程、步骤、章节，如"GB/T XXX的试验步骤"、"标准第3章内容"
4. **NUMERIC**（数值统计）：询问数量、统计、排名，如"有多少设备"、"统计一下项目数量"
5. **EXPLAIN**（解释说明）：询问原理、区别、原因、优缺点，如"为什么需要高温测试"、"A和B的区别"
6. **OTHER**（其他）：不属于以上任何一类

## 输出格式

请严格输出 JSON 格式：
{
  "intent": "意图类型英文大写",
  "reason": "分类理由（中文简述）",
  "target_kind": "目标类型（仅 RELATION_QUERY 需要：project|indicator|method|equipment|unknown）",
  "focus_entity": "焦点实体/关键词（如'无人机'、'高温工作性能测试'）"
}

## 用户问题

{question}
"""


def llm_classify_intent(
    client: Any,
    model: str,
    question: str,
) -> IntentResult:
    """
    使用 LLM 进行意图分类
    
    Args:
        client: OpenAI 客户端
        model: 模型名称
        question: 用户问题
    
    Returns:
        IntentResult: 意图分类结果
    """
    if not client or not question:
        return IntentResult(Intent.OTHER, "LLM 客户端未初始化或问题为空")

    try:
        prompt = LLM_INTENT_CLASSIFY_PROMPT.replace("{question}", question[:500])
        
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一个专业的意图分类器，只输出 JSON。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=200,
        )
        
        content = (response.choices[0].message.content or "").strip()
        
        # 提取 JSON
        json_start = content.find("{")
        json_end = content.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            json_str = content[json_start:json_end]
            result = json.loads(json_str)
            
            intent_str = result.get("intent", "OTHER")
            reason = result.get("reason", "")
            target_kind = result.get("target_kind", "unknown")
            focus_entity = result.get("focus_entity", "")
            
            # 转换为 Intent 枚举
            try:
                intent = Intent(intent_str)
            except ValueError:
                intent = Intent.OTHER
                reason = f"LLM 返回未知意图: {intent_str}"
            
            logger.info(
                "[ZNWD][LLM-Intent] question=%r intent=%s reason=%s target=%s focus=%r",
                question[:50], intent.value, reason, target_kind, focus_entity,
            )
            
            return IntentResult(intent, reason)
        else:
            logger.warning("[ZNWD][LLM-Intent] 未解析到 JSON，回退到规则分类")
            return rule_classify_intent(question)
            
    except Exception as e:
        logger.error("[ZNWD][LLM-Intent] LLM 分类失败: %s，回退到规则分类", e)
        return rule_classify_intent(question)


def classify_intent(
    question: str,
    client: Any = None,
    model: str = "qwen-plus",
) -> IntentResult:
    """
    意图分类入口函数
    
    根据配置决定使用 LLM 还是规则进行分类。
    
    Args:
        question: 用户问题
        client: OpenAI 客户端（仅 LLM 模式需要）
        model: 模型名称（仅 LLM 模式需要）
    
    Returns:
        IntentResult: 意图分类结果
    """
    if is_llm_routing_enabled() and client:
        return llm_classify_intent(client, model, question)
    return rule_classify_intent(question)


def is_llm_intent_enabled() -> bool:
    """检查是否启用了 LLM 意图/路由（与 ZNWD_INTENT_MODE=llm 一致）"""
    return is_llm_routing_enabled()


def relation_target_kind(question: str) -> str:
    """
    对 RELATION_QUERY 粗略推断目标侧：project | indicator | method | equipment | unknown
    
    这个函数仍然使用规则，因为 LLM 已经在 classify_intent 中返回了 target_kind。
    """
    from .intent_classifier import relation_target_kind as rule_target_kind
    return rule_target_kind(question)
