"""
ZNWD5 主流水线：复用 ZNWD4 路由/Playbook/合成，经 bootstrap 注入 Zvec 检索。
"""

from __future__ import annotations

from .bootstrap import apply_znwd5_patches

apply_znwd5_patches()

from blueprints.GLCX.ZNWD4.pipeline import stream_znwd4_chat  # noqa: E402


def stream_znwd5_chat(*args, **kwargs):
    """与 stream_znwd4_chat 参数完全一致。"""
    return stream_znwd4_chat(*args, **kwargs)
