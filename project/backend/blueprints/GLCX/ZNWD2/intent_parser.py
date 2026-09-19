"""意图解析器：LLM → 结构化 Intent JSON。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional

from .intent_prompts import build_intent_system_prompt, build_intent_user_prompt
from .rule_inference import correct_intent_from_rules, infer_intent_from_rules
from .schema import DEFAULT_START_BY_INTENT, DEFAULT_TARGETS_BY_INTENT, INTENT_TYPES, NODE_LABELS
from .types import IntentJson

logger = logging.getLogger(__name__)


class IntentParseError(ValueError):
    """意图解析失败。"""


def validate_intent(raw: Dict[str, Any]) -> IntentJson:
    """校验并补全意图 JSON。"""
    if not isinstance(raw, dict):
        raise IntentParseError("意图必须是 JSON 对象")

    intent_type = str(raw.get("intent_type") or "").strip()
    if intent_type not in INTENT_TYPES:
        raise IntentParseError(f"无效 intent_type: {intent_type}")

    start_entities = raw.get("start_entities")
    if not isinstance(start_entities, list) or not start_entities:
        start_entities = list(DEFAULT_START_BY_INTENT.get(intent_type, ["Equipment"]))
    start_entities = [str(x) for x in start_entities if str(x) in NODE_LABELS]

    target_entities = raw.get("target_entities")
    if not isinstance(target_entities, list) or not target_entities:
        target_entities = list(DEFAULT_TARGETS_BY_INTENT.get(intent_type, ["Indicator"]))
    target_entities = [str(x) for x in target_entities if str(x) in NODE_LABELS]

    start_filters = raw.get("start_filters")
    if start_filters is not None and not isinstance(start_filters, dict):
        raise IntentParseError("start_filters 必须是对象或 null")
    start_filters = {str(k): str(v) for k, v in (start_filters or {}).items() if str(v).strip()}

    env = raw.get("environment_constraint")
    if env is not None:
        if not isinstance(env, dict):
            raise IntentParseError("environment_constraint 必须是对象或 null")
        env = {str(k): str(v) for k, v in env.items() if str(v).strip()} or None

    additional = raw.get("additional_filters")
    if additional is not None:
        if not isinstance(additional, dict):
            raise IntentParseError("additional_filters 必须是对象或 null")
        additional = {str(k): str(v) for k, v in additional.items() if str(v).strip()} or None

    # 指标仅依赖设备：查指标时忽略环境槽位（对齐 ZNWD1 消歧铁律）
    if intent_type == "查指标":
        env = None
        additional = None

    # 列举：全库枚举，不需要锚定过滤
    if intent_type == "列举":
        env = None
        additional = None
        if not start_filters:
            start_filters = None
        if target_entities:
            start_entities = [target_entities[0]]

    return IntentJson(
        intent_type=intent_type,
        start_entities=start_entities,
        start_filters=start_filters or None,
        environment_constraint=env,
        target_entities=target_entities,
        additional_filters=additional,
    )


def _extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise IntentParseError("LLM 返回为空")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            return json.loads(m.group(0))
        raise IntentParseError("无法解析 JSON") from None


def parse_intent(
    user_query: str,
    *,
    client: Any,
    model: str,
    temperature: float = 0.1,
    max_retries: int = 1,
) -> IntentJson:
    """
    调用 LLM 将用户问题解析为意图 JSON。

    Args:
        user_query: 用户自然语言问题
        client: OpenAI 兼容客户端
        model: 模型名称
        temperature: 采样温度
        max_retries: 解析失败时的重试次数

    Returns:
        校验后的 IntentJson
    """
    if not (user_query or "").strip():
        raise IntentParseError("问题不能为空")

    rule_raw = infer_intent_from_rules(user_query)
    if rule_raw:
        return validate_intent(rule_raw)

    if client is None:
        raise IntentParseError("未配置 LLM 客户端")

    system = build_intent_system_prompt()
    user = build_intent_user_prompt(user_query)
    last_err: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            content = resp.choices[0].message.content or ""
            raw = _extract_json(content)
            intent = validate_intent(raw)
            return correct_intent_from_rules(user_query, intent)
        except Exception as e:
            last_err = e
            logger.warning("[ZNWD2] intent parse attempt %d failed: %s", attempt + 1, e)

    raise IntentParseError(str(last_err or "意图解析失败"))


def parse_intent_from_dict(raw: Dict[str, Any]) -> IntentJson:
    """直接从 dict 校验（供单测与调试）。"""
    return validate_intent(raw)
