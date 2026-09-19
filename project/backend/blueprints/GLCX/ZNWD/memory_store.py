"""
Redis-based short-term memory for RAG chat (定长队列 + 异步摘要压缩 + TTL).

Keys:
- memory:{user_id}:{session_id} → {"summary": "...", "last_exchange": {...}}
- messages:{user_id}:{session_id} → Redis LIST of JSON message objects（仅 role/content/timestamp，避免无限膨胀）
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_COMPRESS_GUARD: Dict[str, float] = {}
_COMPRESS_GUARD_LOCK = threading.Lock()


def _safe_redis_call(redis_client, method: str, *args, **kwargs):
    try:
        return getattr(redis_client, method)(*args, **kwargs)
    except Exception as e:
        logger.error("Redis %s error: %s", method, e)
        return None


class RedisMemoryStore:
    PREFIX = "memory"
    MSG_PREFIX = "messages"

    def __init__(
        self,
        redis_client,
        ttl: int = 1800,
        max_messages: int = 10,
        compress_trigger: int = 8,
        keep_recent: int = 4,
    ):
        self._redis = redis_client
        self._ttl = int(ttl)
        self._max_messages = max(2, int(max_messages))
        self._compress_trigger = max(4, int(compress_trigger))
        self._keep_recent = max(2, int(keep_recent))

    def _make_key(self, user_id: str, session_id: str) -> str:
        return f"{self.PREFIX}:{user_id}:{session_id}"

    def _make_msg_key(self, user_id: str, session_id: str) -> str:
        return f"{self.MSG_PREFIX}:{user_id}:{session_id}"

    def get(self, user_id: str, session_id: str) -> Optional[dict]:
        raw = _safe_redis_call(self._redis, "get", self._make_key(user_id, session_id))
        if raw:
            try:
                return json.loads(raw)
            except Exception:
                if isinstance(raw, str):
                    return {"summary": raw, "last_exchange": None}
        return None

    def get_summary(self, user_id: str, session_id: str) -> str:
        data = self.get(user_id, session_id)
        return data.get("summary", "") if data else ""

    def get_last_exchange(self, user_id: str, session_id: str) -> Optional[dict]:
        data = self.get(user_id, session_id)
        return data.get("last_exchange") if data else None

    def get_session_context(self, user_id: str, session_id: str) -> Dict[str, Any]:
        data = self.get(user_id, session_id) or {}
        ctx = data.get("session_context")
        return dict(ctx) if isinstance(ctx, dict) else {}

    def set_session_context(self, user_id: str, session_id: str, session_context: Dict[str, Any]) -> None:
        data = self.get(user_id, session_id) or {}
        data["session_context"] = dict(session_context or {})
        _safe_redis_call(
            self._redis,
            "setex",
            self._make_key(user_id, session_id),
            self._ttl,
            json.dumps(data, ensure_ascii=False),
        )

    def _preserve_session_context(self, user_id: str, session_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        prev = self.get(user_id, session_id) or {}
        ctx = prev.get("session_context")
        if isinstance(ctx, dict) and ctx:
            payload["session_context"] = ctx
        return payload

    def set(self, user_id: str, session_id: str, summary: str, last_exchange: dict = None):
        payload = {"summary": summary or "", "last_exchange": last_exchange}
        payload = self._preserve_session_context(user_id, session_id, payload)
        _safe_redis_call(
            self._redis,
            "setex",
            self._make_key(user_id, session_id),
            self._ttl,
            json.dumps(payload, ensure_ascii=False),
        )

    def update_merge(self, user_id: str, session_id: str, summary: str, user_question: str, assistant_answer: str):
        """兼容旧接口：summary 整段覆盖写入，不再与历史摘要做无限字符串拼接。"""
        new_exchange = {"user": user_question, "assistant": assistant_answer}
        combined = (summary or "").strip()
        payload = {"summary": combined, "last_exchange": new_exchange}
        payload = self._preserve_session_context(user_id, session_id, payload)
        _safe_redis_call(
            self._redis,
            "setex",
            self._make_key(user_id, session_id),
            self._ttl,
            json.dumps(payload, ensure_ascii=False),
        )

    def delete(self, user_id: str, session_id: Optional[str] = None):
        try:
            if session_id:
                self._redis.delete(self._make_key(user_id, session_id))
                self._redis.delete(self._make_msg_key(user_id, session_id))
            else:
                keys = self._redis.keys(f"{self.PREFIX}:{user_id}:*")
                if keys:
                    self._redis.delete(*keys)
                msg_keys = self._redis.keys(f"{self.MSG_PREFIX}:{user_id}:*")
                if msg_keys:
                    self._redis.delete(*msg_keys)
        except Exception as e:
            logger.error("Redis memory delete error: %s", e)

    def get_all_for_user(self, user_id: str) -> dict:
        try:
            pattern = f"{self.PREFIX}:{user_id}:*"
            keys = self._redis.keys(pattern)
            result = {}
            for key in keys:
                session_id = key.split(":")[2] if len(key.split(":")) > 2 else key
                raw = self._redis.get(key)
                if raw:
                    try:
                        result[session_id] = json.loads(raw)
                    except Exception:
                        result[session_id] = {"summary": raw, "last_exchange": None}
            return result
        except Exception as e:
            logger.error("Redis memory get_all_for_user error: %s", e)
            return {}

    def trim_messages(self, user_id: str, session_id: str, max_len: Optional[int] = None) -> None:
        """丢弃最早的消息，保持列表长度不超过 max_len（lpop 一端为最早写入）。"""
        limit = int(max_len or self._max_messages)
        key = self._make_msg_key(user_id, session_id)
        for _ in range(5000):
            cnt = _safe_redis_call(self._redis, "llen", key)
            cnt = int(cnt or 0)
            if cnt <= limit:
                break
            _safe_redis_call(self._redis, "lpop", key)
        _safe_redis_call(self._redis, "expire", key, self._ttl)

    def add_message(self, user_id: str, session_id: str, role: str, content: str, thinking_steps: list = None):
        msg: Dict[str, Any] = {
            "role": role,
            "content": (content or "")[:8000],
            "timestamp": datetime.now().isoformat(),
        }
        if thinking_steps:
            msg["thinkingSteps"] = thinking_steps
        key = self._make_msg_key(user_id, session_id)
        _safe_redis_call(self._redis, "rpush", key, json.dumps(msg, ensure_ascii=False))
        _safe_redis_call(self._redis, "expire", key, self._ttl)
        self.trim_messages(user_id, session_id, self._max_messages)

    def get_messages(self, user_id: str, session_id: str) -> List[dict]:
        key = self._make_msg_key(user_id, session_id)
        raw_list = _safe_redis_call(self._redis, "lrange", key, 0, -1)
        if raw_list:
            out: List[dict] = []
            for m in raw_list:
                try:
                    out.append(json.loads(m))
                except Exception:
                    continue
            return out
        return []

    def clear_messages(self, user_id: str, session_id: str):
        key = self._make_msg_key(user_id, session_id)
        _safe_redis_call(self._redis, "delete", key)

    def replace_messages(self, user_id: str, session_id: str, messages: List[dict]) -> None:
        """原子替换消息列表（压缩后写回最近若干条）。"""
        key = self._make_msg_key(user_id, session_id)
        try:
            pipe = self._redis.pipeline()
            pipe.delete(key)
            for m in messages:
                pipe.rpush(key, json.dumps(m, ensure_ascii=False))
            pipe.expire(key, self._ttl)
            pipe.execute()
        except Exception as e:
            logger.error("replace_messages failed: %s", e)

    def get_message_count(self, user_id: str, session_id: str) -> int:
        key = self._make_msg_key(user_id, session_id)
        count = _safe_redis_call(self._redis, "llen", key)
        return int(count) if count is not None else 0

    def set_summary(self, user_id: str, session_id: str, summary: str):
        data = self.get(user_id, session_id) or {}
        data["summary"] = (summary or "").strip()
        _safe_redis_call(
            self._redis,
            "setex",
            self._make_key(user_id, session_id),
            self._ttl,
            json.dumps(data, ensure_ascii=False),
        )

    def compress_conversation(self, user_id: str, session_id: str, llm_client, model: str) -> None:
        """
        将早期消息压缩为短摘要（≤200字），保留最近 keep_recent 条消息。
        应在后台线程调用；同一 session 短时间内防重入。
        """
        if not llm_client or not model:
            return
        guard_key = f"{user_id}:{session_id}"
        now = time.time()
        with _COMPRESS_GUARD_LOCK:
            last = _COMPRESS_GUARD.get(guard_key, 0)
            if now - last < 3.0:
                return
            _COMPRESS_GUARD[guard_key] = now

        msgs = self.get_messages(user_id, session_id)
        if len(msgs) <= self._compress_trigger:
            return

        keep_n = min(self._keep_recent, len(msgs))
        head = msgs[:-keep_n] if keep_n else msgs
        tail = msgs[-keep_n:] if keep_n else []

        lines = []
        for m in head:
            role = str(m.get("role") or "")
            body = str(m.get("content") or "")[:600]
            lines.append(f"{role}: {body}")
        dialogue = "\n".join(lines)[:12000]
        old_summary = self.get_summary(user_id, session_id)[:800]

        prompt = (
            "请将「旧摘要」与「早期多轮对话」合并为一段不超过200字的中文摘要，"
            "保留用户关心的主题、设备、环境、标准与未决问题；不要 Markdown，不要列 JSON。\n\n"
            f"旧摘要：{old_summary}\n\n早期对话：\n{dialogue}"
        )
        new_summary = old_summary
        try:
            resp = llm_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=400,
            )
            new_summary = (resp.choices[0].message.content or "").strip()[:400]
        except Exception as e:
            logger.warning("[memory] compress LLM failed: %s", e)

        if not new_summary:
            new_summary = old_summary[:200]

        self.set_summary(user_id, session_id, new_summary[:200])
        slim_tail: List[dict] = []
        for m in tail:
            slim_tail.append(
                {
                    "role": m.get("role") or "user",
                    "content": str(m.get("content") or "")[:8000],
                    "timestamp": m.get("timestamp") or datetime.now().isoformat(),
                }
            )
        self.replace_messages(user_id, session_id, slim_tail)
        logger.info(
            "[memory] compressed session=%s user=%s kept=%d new_summary_len=%d",
            session_id[:12],
            user_id[:16],
            len(slim_tail),
            len(new_summary),
        )

    def maybe_compress_async(self, user_id: str, session_id: str, llm_client, model: str) -> None:
        if self.get_message_count(user_id, session_id) > self._compress_trigger:
            t = threading.Thread(
                target=self.compress_conversation,
                args=(user_id, session_id, llm_client, model),
                daemon=True,
            )
            t.start()


_memory_store_instance: Optional["RedisMemoryStore"] = None


def init_memory_store(
    redis_client,
    ttl: int = 1800,
    max_messages: int = 10,
    compress_trigger: int = 8,
    keep_recent: int = 4,
) -> RedisMemoryStore:
    global _memory_store_instance
    _memory_store_instance = RedisMemoryStore(
        redis_client,
        ttl=ttl,
        max_messages=max_messages,
        compress_trigger=compress_trigger,
        keep_recent=keep_recent,
    )
    return _memory_store_instance


def get_memory_store() -> Optional[RedisMemoryStore]:
    return _memory_store_instance
