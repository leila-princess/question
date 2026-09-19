"""
ZNWD V6：入口保留在 `services_v6.py`，实现已迁至 `znwd_langchain_v6`（LangChain + 工具）与 `znwd_v6_tools`（工具实现）。
"""

from .znwd_langchain_v6 import llm_stream_single_v6

__all__ = ("llm_stream_single_v6",)
