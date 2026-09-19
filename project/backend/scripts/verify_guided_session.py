"""对引导式会话做 materialize 抽检（开发调试用）。"""
import os
import re
import sys
from typing import List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from blueprints.FLGL.guided_method_extract import (
    _load_session,
    _resolve_method_boundary_span,
    _run_materialize,
)


def main(session_id: str, candidate_ids: Optional[List[str]] = None) -> None:
    session = _load_session(session_id)
    if not session:
        raise SystemExit(f"session not found: {session_id}")
    methods = _run_materialize(session, candidate_ids=candidate_ids)
    full_text = str(session.get("full_text") or "")
    full_text = str(session.get("full_text") or "")
    sections = list((session.get("parse_cache") or {}).get("sections") or [])
    for m in methods:
        cid = m.get("candidate_id")
        merged = m.get("merged_len") or 0
        print(f"{cid}: merged_len={merged}")
        chunk_path = os.path.join(
            session["session_dir"], "materialized", f"{cid}_chunk.html"
        )
        if os.path.isfile(chunk_path):
            with open(chunk_path, encoding="utf-8") as f:
                plain = re.sub(r"<[^>]+>", "\n", f.read()).strip()
            print(f"  head: {plain[:80]!r}")
            bad = []
            if "7.1 温度" in plain and cid in ("C001", "C002", "C003", "C004", "C005"):
                bad.append("误抓7.1设备节")
            if plain.startswith("与操作") and cid == "C012":
                bad.append("误含上一节8.5.2")
            if bad:
                print(f"  FAIL: {', '.join(bad)}")
            else:
                print("  OK")
        cand = next((c for c in (session.get("candidates") or []) if c.get("candidate_id") == cid), {})
        if cand.get("method_scope") == "full_project" or cand.get("boundary_start"):
            span = _resolve_method_boundary_span(cand, sections, full_text)
            if span:
                print(f"  boundary_span: {span.get('chunk_len')} chars")


if __name__ == "__main__":
    sid = sys.argv[1] if len(sys.argv) > 1 else "df70f20f0a5f46d180f6236742d80acf"
    cids = sys.argv[2:] if len(sys.argv) > 2 else ["C001", "C012"]
    main(sid, cids or None)
