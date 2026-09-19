"""Re-merge target files with git merge-file (3-way), prefer ours on remaining conflicts."""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MERGE_BOTH = [
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

CONFLICT_RE = re.compile(
    r"<<<<<<<[^\n\r]*[\n\r]+(.*?)=======[\n\r]+(.*?)>>>>>>>[^\n\r]*(?:[\n\r]|$)",
    re.DOTALL,
)


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=check)


def show_ref(ref: str, rel: str) -> str | None:
    r = run(["git", "show", f"{ref}:{rel}"], check=False)
    if r.returncode != 0:
        return None
    return r.stdout


def merge_base() -> str | None:
    r = run(["git", "merge-base", "HEAD", "MERGE_HEAD"], check=False)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return r.stdout.strip()


def resolve_prefer_ours(text: str) -> str:
    text = text.replace("\r\n", "\n")
    while True:
        m = CONFLICT_RE.search(text)
        if not m:
            break
        a, b = m.group(1).rstrip("\n"), m.group(2).rstrip("\n")
        if not a:
            merged = b
        elif not b:
            merged = a
        elif a == b:
            merged = a
        else:
            a_lines = a.splitlines()
            b_lines = b.splitlines()
            extra = [ln for ln in b_lines if ln not in a_lines]
            merged = a + ("\n" + "\n".join(extra) if extra else "")
        text = text[: m.start()] + merged + text[m.end() :]
    return text


def main() -> int:
    failed: list[str] = []
    mb = merge_base()
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        for rel in MERGE_BOTH:
            ours = show_ref("HEAD", rel)
            theirs = show_ref("MERGE_HEAD", rel)
            base = show_ref(mb, rel) if mb else None
            if ours is None:
                print(f"SKIP no ours: {rel}")
                continue
            if theirs is None:
                out = ours
            elif base is None:
                out = resolve_prefer_ours(
                    f"<<<<<<< ours\n{ours}=======\n{theirs}>>>>>>> theirs\n"
                )
            else:
                base_f = tdp / "base"
                ours_f = tdp / "ours"
                theirs_f = tdp / "theirs"
                base_f.write_text(base, encoding="utf-8")
                ours_f.write_text(ours, encoding="utf-8")
                theirs_f.write_text(theirs, encoding="utf-8")
                r = run(
                    ["git", "merge-file", "-p", str(ours_f), str(base_f), str(theirs_f)],
                    check=False,
                )
                out = r.stdout if r.returncode <= 1 else ours
                if "<<<<<<<" in out:
                    out = resolve_prefer_ours(out)
            if "<<<<<<<" in out or ">>>>>>>" in out:
                failed.append(rel)
                print(f"FAILED markers: {rel}")
                continue
            (ROOT / rel).write_text(out, encoding="utf-8")
            run(["git", "add", "--", rel])
            print(f"OK: {rel}")
    print(f"failed={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
