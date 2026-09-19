"""Load MLP route classifier bundle for ZNWD4 production routing."""

from __future__ import annotations

import logging
import os
import pickle
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_ZNWD4_ROOT = Path(__file__).resolve().parent
_DEFAULT_BUNDLE_CANDIDATES = (
    _ZNWD4_ROOT / "models" / "router_mlp.pkl",
    _ZNWD4_ROOT / "models" / "router_mlp_exp4_qwen25.pkl",
    _ZNWD4_ROOT.parents[3] / "EFHR" / "experiments" / "case_a" / "exp4_qwen25" / "models" / "router_mlp_exp4_qwen25.pkl",
)


def resolve_mlp_bundle_path() -> Path:
    env_path = (os.environ.get("ZNWD4_MLP_BUNDLE_PATH") or "").strip()
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
        raise FileNotFoundError(f"ZNWD4_MLP_BUNDLE_PATH not found: {p}")
    for candidate in _DEFAULT_BUNDLE_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "MLP router bundle not found. Set ZNWD4_MLP_BUNDLE_PATH or place router_mlp.pkl under "
        f"{_ZNWD4_ROOT / 'models'}"
    )


@lru_cache(maxsize=1)
def load_mlp_bundle() -> Dict[str, Any]:
    path = resolve_mlp_bundle_path()
    with path.open("rb") as f:
        bundle = pickle.load(f)
    logger.info("[ZNWD4][mlp] loaded bundle from %s (n_train=%s)", path, (bundle.get("meta") or {}).get("n_train"))
    return bundle


def predict_mlp_route(question: str, bundle: Optional[Dict[str, Any]] = None) -> Tuple[str, float]:
    """Return (route_id, confidence). Confidence in [0,1] when predict_proba is available."""
    bundle = bundle or load_mlp_bundle()
    q = (question or "").strip()
    if not q:
        return str(bundle.get("majority_route") or ""), 0.0

    pipe = bundle.get("mlp")
    if pipe is None:
        return str(bundle.get("majority_route") or ""), 0.0

    pred = pipe.predict([q])
    val = pred[0]
    le = bundle.get("label_encoder")
    route_id = str(val)
    if le is not None:
        try:
            route_id = str(le.inverse_transform([int(val)])[0])
        except (ValueError, TypeError):
            route_id = str(val)

    confidence = 0.0
    try:
        proba = pipe.predict_proba([q])[0]
        confidence = float(max(proba))
    except Exception:
        confidence = 1.0

    return route_id, confidence


def predict_mlp_route_topk(
    question: str,
    bundle: Optional[Dict[str, Any]] = None,
    *,
    top_k: int = 4,
) -> List[Tuple[str, float]]:
    """Return [(route_id, confidence), ...] sorted by confidence desc."""
    import numpy as np

    bundle = bundle or load_mlp_bundle()
    q = (question or "").strip()
    k = max(1, int(top_k))
    if not q:
        maj = str(bundle.get("majority_route") or "")
        return [(maj, 0.0)] if maj else []

    pipe = bundle.get("mlp")
    le = bundle.get("label_encoder")
    if pipe is None:
        maj = str(bundle.get("majority_route") or "")
        return [(maj, 0.0)] if maj else []

    try:
        proba = pipe.predict_proba([q])[0]
        idx = np.argsort(proba)[::-1][:k]
        out: List[Tuple[str, float]] = []
        for i in idx:
            if le is not None:
                try:
                    rid = str(le.inverse_transform([int(i)])[0])
                except (ValueError, TypeError):
                    rid = str(i)
            else:
                rid = str(pipe.classes_[int(i)]) if hasattr(pipe, "classes_") else str(i)
            out.append((rid, float(proba[int(i)])))
        return out
    except Exception:
        rid, conf = predict_mlp_route(q, bundle)
        return [(rid, conf)]


__all__ = ["load_mlp_bundle", "predict_mlp_route", "predict_mlp_route_topk", "resolve_mlp_bundle_path"]
