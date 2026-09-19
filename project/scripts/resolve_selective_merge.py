"""Selective merge conflict resolution.

- MERGE_BOTH: 分类管理解析 + 关联查询三栏组件 — keep both sides
- Everything else: keep local (ours / HEAD)
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 分类管理（FLGL）解析相关 + 关联查询（JQCX）三栏组件相关
MERGE_BOTH = {
    p.replace("\\", "/")
    for p in [
        "backend/blueprints/FLGL/GetTestMethod/parser.py",
        "backend/blueprints/FLGL/GetTestMethod/prompts.py",
        "backend/blueprints/FLGL/method_block_markdown.py",
        "backend/blueprints/FLGL/method_routes.py",
        "backend/blueprints/FLGL/nodes_routes.py",
        "backend/blueprints/FLGL/pdf_ocr_extractor.py",
        "backend/blueprints/FLGL/routes.py",
        "backend/blueprints/FLGL/test_extrac_output.py",
        "fronted/app/FLGL/api.ts",
        "fronted/app/FLGL/components/EnvironmentPanel.tsx",
        "fronted/app/FLGL/components/GenericNodePanel.tsx",
        "fronted/app/FLGL/components/IndicatorPanel.tsx",
        "fronted/app/FLGL/components/MethodPanel.tsx",
        "fronted/app/FLGL/page.tsx",
        "fronted/app/FLGL_method/[...path]/route.ts",
        "backend/blueprints/GLCX/JQCX/routes.py",
        "backend/blueprints/GLCX/methods_shared.py",
        "fronted/app/GLCX/JQCX/api.ts",
        "fronted/app/api/GLCX/JQCX/route.ts",
        "fronted/components/EnvironmentTable/index.tsx",
        "fronted/components/ThreeColumnLayout/EnvironmentFiveElementsModal.tsx",
        "fronted/components/ThreeColumnLayout/MixedContent.tsx",
        "fronted/utils/layoutConfig.ts",
    ]
}

CONFLICT_RE = re.compile(
    r"<<<<<<<[^\n\r]*[\n\r]+(.*?)=======[\n\r]+(.*?)>>>>>>>[^\n\r]*(?:[\n\r]|$)",
    re.DOTALL,
)


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=check,
    )


def unmerged_files() -> list[str]:
    r = run("git", "diff", "--name-only", "--diff-filter=U")
    return [f.strip().replace("\\", "/") for f in r.stdout.splitlines() if f.strip()]


def resolve_keep_both(text: str) -> str:
    text = text.replace("\r\n", "\n")
    while True:
        m = CONFLICT_RE.search(text)
        if not m:
            break
        a, b = m.group(1).rstrip("\n"), m.group(2).rstrip("\n")
        if a == b:
            merged = a
        elif not a:
            merged = b
        elif not b:
            merged = a
        else:
            merged = a + "\n\n" + b
        text = text[: m.start()] + merged + text[m.end() :]
    return text


def main() -> int:
    files = unmerged_files()
    if not files:
        print("No unmerged files.")
        return 0

    merge_both_done: list[str] = []
    ours_done: list[str] = []
    failed: list[str] = []

    for rel in files:
        norm = rel.replace("\\", "/")
        if norm in MERGE_BOTH:
            run("git", "checkout", "-m", "--", rel, check=False)
            path = ROOT / rel
            raw = path.read_text(encoding="utf-8", errors="replace")
            out = resolve_keep_both(raw)
            if "<<<<<<<" in out or ">>>>>>>" in out:
                failed.append(rel)
                print(f"MERGE_BOTH FAILED: {rel}")
                continue
            path.write_text(out, encoding="utf-8")
            run("git", "add", "--", rel)
            merge_both_done.append(rel)
            print(f"MERGE_BOTH OK: {rel}")
        else:
            r = run("git", "checkout", "--ours", "--", rel, check=False)
            if r.returncode != 0:
                failed.append(rel)
                print(f"OURS FAILED: {rel} — {r.stderr.strip()}")
                continue
            run("git", "add", "--", rel)
            ours_done.append(rel)
            print(f"OURS OK: {rel}")

    print(f"\nSummary: merge_both={len(merge_both_done)}, ours={len(ours_done)}, failed={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
