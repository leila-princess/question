"""PDF 提取后端扩展（MinerU 等）。"""

from .mineru_adapter import (
    is_mineru_available,
    parse_pdf_with_mineru,
    mineru_availability_detail,
)

__all__ = [
    "is_mineru_available",
    "parse_pdf_with_mineru",
    "mineru_availability_detail",
]
