"""GetTestMethod package.

保持包入口极简，避免在 import GetTestMethod 时同时加载
PaddleOCR 与 sentence-transformers / torch，导致 Windows DLL 冲突。
"""

__all__ = []
