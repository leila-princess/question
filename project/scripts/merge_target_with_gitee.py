"""Merge local HEAD with origin/master for FLGL parse + JQCX three-column files."""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MERGE_FILES = [
    "backend/blueprints/FLGL/GetTestMethod/parser.py",
    "backend/blueprints/FLGL/GetTestMethod/prompts.py",
    "backend/blueprints/FLGL/method_block_markdown.py",
    "backend/blueprints/FLGL/method_routes.py",
    "backend/blueprints/FLGL/nodes_routes.py",
    "backend/blueprints/FLGL/pdf_ocr_extractor.py",
    "backend/blueprints/FLGL/routes.py",
    "backend/blueprints/FLGL/test_extrac_output.py",
    "backend/blueprints/FLGL/env_parser.py",
    "backend/blueprints/FLGL/upload_routes.py",
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
    "fronted/app/GLCX/JQCX/page.tsx",
    "fronted/app/api/GLCX/JQCX/route.ts",
    "fronted/components/EnvironmentTable/index.tsx",
    "fronted/components/ThreeColumnLayout/index.tsx",
    "fronted/components/ThreeColumnLayout/EnvironmentFiveElementsModal.tsx",
    "fronted/components/ThreeColumnLayout/MixedContent.tsx",
    "fronted/utils/layoutConfig.ts",
]

CONFLICT_RE = re.compile(
    r"<<<<<<<[^\n\r]*[\n\r]+(.*?)=======[\n\r]+(.*?)>>>>>>>[^\n\r]*(?:[\n\r]|$)",
    re.DOTALL,
)


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def show(ref: str, rel: str) -> str | None:
    r = run(["git", "show", f"{ref}:{rel}"])
    if r.returncode != 0:
        return None
    return r.stdout


def merge_base() -> str | None:
    r = run(["git", "merge-base", "HEAD", "origin/master"])
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return r.stdout.strip()


def resolve_conflicts_prefer_local(text: str) -> str:
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
            extra = [ln for ln in b.splitlines() if ln and ln not in a.splitlines()]
            merged = a + ("\n" + "\n".join(extra) if extra else "")
        text = text[: m.start()] + merged + text[m.end() :]
    return text


def main() -> int:
    mb = merge_base() or "origin/master"
    changed = 0
    skipped = 0
    failed: list[str] = []

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        for rel in MERGE_FILES:
            local = show("HEAD", rel)
            remote = show("origin/master", rel)
            base = show(mb, rel)
            if local is None:
                print(f"SKIP local missing: {rel}")
                skipped += 1
                continue
            if remote is None:
                print(f"KEEP local only: {rel}")
                continue
            if remote == local:
                print(f"UNCHANGED: {rel}")
                continue
            if base is None:
                base = remote
            out_f = tdp / "out"
            base_f = tdp / "base"
            local_f = tdp / "local"
            remote_f = tdp / "remote"
            out_f.write_text(local, encoding="utf-8")
            base_f.write_text(base, encoding="utf-8")
            local_f.write_text(local, encoding="utf-8")
            remote_f.write_text(remote, encoding="utf-8")
            r = run(
                ["git", "merge-file", "-p", str(out_f), str(base_f), str(remote_f)],
            )
            out = r.stdout if r.returncode <= 1 and r.stdout else local
            if "<<<<<<<" in out:
                out = resolve_conflicts_prefer_local(out)
            if "<<<<<<<" in out or ">>>>>>>" in out:
                failed.append(rel)
                print(f"FAILED: {rel}")
                continue
            if out != local:
                (ROOT / rel).write_text(out, encoding="utf-8")
                run(["git", "add", "--", rel])
                changed += 1
                print(f"MERGED: {rel}")
            else:
                print(f"SAME after merge: {rel}")

    print(f"\nchanged={changed}, skipped={skipped}, failed={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
