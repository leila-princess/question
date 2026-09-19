"""Resolve git merge conflicts by keeping both sides (ours then theirs)."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CONFLICT_RE = re.compile(
    r"<<<<<<<[^\n\r]*[\n\r]+(.*?)=======[\n\r]+(.*?)>>>>>>>[^\n\r]*(?:[\n\r]|$)",
    re.DOTALL,
)

# Lockfiles cannot be concatenated; keep local (HEAD) version only.
KEEP_OURS_ONLY = {"fronted/package-lock.json"}


def resolve_keep_both(text: str) -> str:
    while True:
        m = CONFLICT_RE.search(text)
        if not m:
            break
        a, b = m.group(1), m.group(2)
        if a.rstrip() == b.rstrip():
            merged = a
        else:
            merged = a.rstrip() + "\n\n" + b.lstrip()
        text = text[: m.start()] + merged + text[m.end() :]
    return text


def resolve_keep_ours(text: str) -> str:
    while True:
        m = CONFLICT_RE.search(text)
        if not m:
            break
        text = text[: m.start()] + m.group(1) + text[m.end() :]
    return text


def main() -> int:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    files = [f.strip() for f in result.stdout.splitlines() if f.strip()]
    unresolved: list[str] = []

    for rel in files:
        path = ROOT / rel
        if not path.exists():
            print(f"SKIP missing: {rel}")
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        if rel.replace("\\", "/") in KEEP_OURS_ONLY:
            out = resolve_keep_ours(raw)
        else:
            out = resolve_keep_both(raw)
        if "<<<<<<<" in out or ">>>>>>>" in out:
            unresolved.append(rel)
            print(f"UNRESOLVED: {rel}")
            continue
        path.write_text(out, encoding="utf-8")
        print(f"OK: {rel}")

    if unresolved:
        print(f"\nFailed to resolve {len(unresolved)} file(s)")
        return 1
    print(f"\nResolved {len(files) - len(unresolved)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
