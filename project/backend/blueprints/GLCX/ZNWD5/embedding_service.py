"""
ZNWD5 统一嵌入服务：优先 transformers 本地 BGE，兼容 SentenceTransformer 缓存。
"""

from __future__ import annotations

import logging
import os
from typing import Any, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

_EMBEDDING_DIM = 512
_EMBEDDING_BACKEND = ""  # transformers | sentence_transformers | hash


def embedding_dim() -> int:
    return _EMBEDDING_DIM


def embedding_backend() -> str:
    return _EMBEDDING_BACKEND or "unknown"


def using_hash_fallback() -> bool:
    return _EMBEDDING_BACKEND == "hash"


def _set_backend(name: str) -> None:
    global _EMBEDDING_BACKEND
    _EMBEDDING_BACKEND = name


def _resolve_model_path() -> str:
    from config import Config

    custom = (os.environ.get("ZVEC_EMBEDDING_MODEL") or "").strip()
    if custom and os.path.exists(custom):
        return os.path.abspath(custom)
    bge = getattr(Config, "FLGL_TESTMETHOD_BGE_MODEL_PATH", None) or ""
    if bge and os.path.exists(os.path.abspath(bge)):
        return os.path.abspath(bge)
    return os.path.abspath(getattr(Config, "LOCAL_EMBEDDING_MODEL_PATH", ""))


def _apply_hf_hub_compat_shim() -> None:
    """兼容旧版 sentence-transformers 对 cached_download 的依赖。"""
    try:
        import huggingface_hub

        if not hasattr(huggingface_hub, "cached_download"):
            from huggingface_hub import hf_hub_download

            huggingface_hub.cached_download = hf_hub_download  # type: ignore[attr-defined]
    except Exception:
        pass


def _apply_transformers_torch_compat() -> None:
    """transformers 4.57+ 在 torch<2.1 时会禁用 PyTorch backend。"""
    try:
        import torch  # noqa: F401
        import transformers.utils.import_utils as iu

        if not iu._torch_available:
            iu._torch_available = True
            if iu._torch_version == "N/A" or iu._torch_version < "2.1.0":
                iu._torch_version = "2.1.0"
    except Exception:
        pass


class _TransformersBgeModel:
    """用 transformers 直接加载本地 bge-small-zh-v1.5，绕过 sentence-transformers 依赖冲突。"""

    def __init__(self, model_path: str, *, local_files_only: bool = True):
        _apply_hf_hub_compat_shim()
        _apply_transformers_torch_compat()
        import torch
        from transformers import BertModel, BertTokenizer

        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._tokenizer = BertTokenizer.from_pretrained(
            model_path,
            local_files_only=local_files_only,
        )
        self._model = BertModel.from_pretrained(
            model_path,
            local_files_only=local_files_only,
        )
        self._model.eval()
        self._model.to(self._device)

    def encode(
        self,
        sentences: List[str],
        *,
        normalize_embeddings: bool = True,
        convert_to_numpy: bool = True,
        show_progress_bar: bool = False,
        batch_size: int = 32,
        **_kwargs: Any,
    ) -> np.ndarray:
        import torch

        del show_progress_bar
        out: List[np.ndarray] = []
        for i in range(0, len(sentences), max(1, batch_size)):
            batch = sentences[i : i + batch_size]
            encoded = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
            encoded = {k: v.to(self._device) for k, v in encoded.items()}
            with torch.no_grad():
                model_out = self._model(**encoded)
                # bge-small-zh-v1.5 使用 [CLS] pooling（见 1_Pooling/config.json）
                pooled = model_out.last_hidden_state[:, 0]
                if normalize_embeddings:
                    pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
                vecs = pooled.cpu().numpy() if convert_to_numpy else pooled
            out.append(np.asarray(vecs, dtype=np.float32))
        if not out:
            return np.zeros((0, _EMBEDDING_DIM), dtype=np.float32)
        return np.vstack(out)


def _load_sentence_transformer(path: str, local_files_only: bool) -> Any:
    _apply_hf_hub_compat_shim()
    _apply_transformers_torch_compat()
    from sentence_transformers import SentenceTransformer

    try:
        return SentenceTransformer(path, local_files_only=local_files_only)
    except TypeError:
        return SentenceTransformer(path)


def get_embedding_model():
    from config import Config

    cached = getattr(Config, "ZNWD5_EMBEDDING_MODEL", None)
    if cached is not None:
        if not _EMBEDDING_BACKEND:
            _set_backend(getattr(Config, "ZNWD5_EMBEDDING_BACKEND", "") or "transformers")
        return cached

    preloaded = getattr(Config, "FLGL_TESTMETHOD_BGE_MODEL", None)
    if preloaded is not None:
        Config.ZNWD5_EMBEDDING_MODEL = preloaded
        backend = getattr(Config, "ZNWD5_EMBEDDING_BACKEND", "") or "transformers"
        _set_backend(backend)
        return preloaded

    path = _resolve_model_path()
    if not path or not os.path.isdir(path):
        raise FileNotFoundError(f"BGE 模型目录不存在: {path!r}")

    lfo = os.environ.get("FLGL_TESTMETHOD_BGE_LOCAL_FILES_ONLY", "1").lower() in ("1", "true", "yes")
    model = None
    backend = ""

    try:
        logger.info("[ZNWD5] 加载 BGE (transformers): %s local_files_only=%s", path, lfo)
        model = _TransformersBgeModel(path, local_files_only=lfo)
        _ = model.encode(["BGE 预加载探针"], normalize_embeddings=True, convert_to_numpy=True)
        backend = "transformers"
    except Exception as exc:
        logger.warning("[ZNWD5] transformers BGE 加载失败，尝试 sentence-transformers: %s", exc)
        try:
            model = _load_sentence_transformer(path, lfo)
            _ = model.encode(["BGE 预加载探针"], normalize_embeddings=True, convert_to_numpy=True)
            backend = "sentence_transformers"
        except Exception as exc2:
            logger.error("[ZNWD5] sentence-transformers BGE 也失败: %s", exc2)
            raise

    Config.ZNWD5_EMBEDDING_MODEL = model
    Config.ZNWD5_EMBEDDING_BACKEND = backend
    Config.FLGL_TESTMETHOD_BGE_MODEL = model
    _set_backend(backend)
    logger.info("[ZNWD5] BGE 就绪 backend=%s path=%s", backend, path)
    return model


def _hash_embed(text: str, dim: int = _EMBEDDING_DIM) -> np.ndarray:
    """BGE 不可用时的确定性伪向量（仅用于索引结构验证，语义质量有限）。"""
    import hashlib

    digest = hashlib.sha256((text or "").encode("utf-8")).digest()
    raw = np.frombuffer(digest * (dim // len(digest) + 1), dtype=np.uint8)[:dim].astype(np.float32)
    norm = np.linalg.norm(raw) + 1e-8
    return (raw / norm).astype(np.float32)


def encode_texts(texts: List[str], *, batch_size: int = 32) -> np.ndarray:
    cleaned = [(t or "").strip() for t in texts]
    if not cleaned:
        return np.zeros((0, _EMBEDDING_DIM), dtype=np.float32)
    try:
        model = get_embedding_model()
        vecs = model.encode(
            cleaned,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
            batch_size=batch_size,
        )
        return np.asarray(vecs, dtype=np.float32)
    except Exception as exc:
        logger.warning("[ZNWD5] BGE encode 失败，使用 hash 伪向量: %s", exc)
        _set_backend("hash")
        return np.stack([_hash_embed(t) for t in cleaned], axis=0)


def encode_query(text: str) -> List[float]:
    q = (text or "").strip()
    if not q:
        return [0.0] * _EMBEDDING_DIM
    arr = encode_texts([q])
    return arr[0].tolist()


def node_to_text(node: dict) -> str:
    """与 FLGL GraphEmbeddingRetriever 一致的节点文本化。"""
    parts = []
    for field_name, cn_name in (
        ("name", "名称"),
        ("label", "标签"),
        ("type", "类别"),
        ("description", "描述"),
    ):
        value = str(node.get(field_name) or "").strip()
        if value:
            parts.append(f"{cn_name}：{value}")
    return "；".join(parts).strip()
