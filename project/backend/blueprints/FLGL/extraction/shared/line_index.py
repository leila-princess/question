"""全文行号索引：供文档解剖师输入与按行切片。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class LineIndex:
    """1-based 行号索引。"""

    lines: List[str]
    line_starts: List[int]  # 0-based char offset per line

    @classmethod
    def build(cls, text: str) -> "LineIndex":
        raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        lines = raw.split("\n")
        starts: List[int] = []
        pos = 0
        for ln in lines:
            starts.append(pos)
            pos += len(ln) + 1
        return cls(lines=lines, line_starts=starts)

    @property
    def line_count(self) -> int:
        return len(self.lines)

    def format_line_numbered(
        self,
        start_line: int = 1,
        end_line: Optional[int] = None,
        *,
        prefix_context: str = "",
    ) -> str:
        """生成 [n] content 文本，供 LLM 输入。"""
        end = end_line if end_line is not None else self.line_count
        start = max(1, start_line)
        end = min(self.line_count, end)
        parts: List[str] = []
        if prefix_context.strip():
            parts.append(prefix_context.strip())
        for i in range(start, end + 1):
            parts.append(f"[{i}] {self.lines[i - 1]}")
        return "\n".join(parts)

    def slice_lines(self, start_line: int, end_line: int) -> str:
        if self.line_count == 0:
            return ""
        s = max(1, start_line)
        e = min(self.line_count, end_line)
        if s > e:
            return ""
        return "\n".join(self.lines[s - 1 : e])

    def line_text(self, line_no: int) -> str:
        if 1 <= line_no <= self.line_count:
            return self.lines[line_no - 1]
        return ""

    def find_section_break_lines(self) -> List[Tuple[int, str]]:
        """返回一级节标题行号（如 8.1、8.2）。"""
        out: List[Tuple[int, str]] = []
        pat = re.compile(r"^(\d+(?:\.\d+)*)\s+[\u4e00-\u9fff]")
        for i, ln in enumerate(self.lines, start=1):
            stripped = ln.strip()
            if not stripped:
                continue
            m = pat.match(stripped)
            if m:
                parts = m.group(1).split(".")
                if len(parts) <= 2:
                    out.append((i, stripped))
        return out

    def validate_range(self, start_line: int, end_line: int) -> bool:
        if start_line < 1 or end_line < start_line:
            return False
        return end_line <= self.line_count
