#!/usr/bin/env python3
"""Check offline wheel coverage for lmdeploy on cp310 Linux."""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_wheel_name(filename: str) -> tuple[str, str]:
    # name-version-...whl (PEP 427)
    parts = filename.replace(".whl", "").split("-")
    if len(parts) < 2:
        return filename, ""
    return parts[0], parts[1]


def read_requires_dist(whl_path: Path) -> list[str]:
    reqs: list[str] = []
    with zipfile.ZipFile(whl_path) as zf:
        meta_name = next(
            (n for n in zf.namelist() if n.endswith(".dist-info/METADATA")),
            None,
        )
        if not meta_name:
            return reqs
        text = zf.read(meta_name).decode("utf-8", errors="replace")
        for line in text.splitlines():
            if line.startswith("Requires-Dist:"):
                reqs.append(line.split(":", 1)[1].strip())
    return reqs


def req_name(req: str) -> str:
    # strip extras and version markers
    req = req.split(";")[0].strip()
    req = re.split(r"[<>=!~]", req, maxsplit=1)[0].strip()
    return normalize_name(req)


def main() -> int:
    wheels_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "0616/packages/pip_wheels_linux")
    if not wheels_dir.is_dir():
        print(f"ERROR: not a directory: {wheels_dir}")
        return 1

    available: dict[str, list[str]] = {}
    for whl in sorted(wheels_dir.glob("*.whl")):
        pkg, ver = parse_wheel_name(whl.name)
        key = normalize_name(pkg)
        available.setdefault(key, []).append(f"{pkg}=={ver}")

    def find_wheel(pkg: str) -> str | None:
        key = normalize_name(pkg)
        vers = available.get(key)
        return vers[0] if vers else None

    missing: set[str] = set()
    checked: set[str] = set()

    def walk(pkg: str, depth: int = 0) -> None:
        key = normalize_name(pkg)
        if key in checked:
            return
        checked.add(key)
        whl_match = find_wheel(pkg)
        if not whl_match:
            missing.add(pkg)
            return
        whl_file = next(
            p for p in wheels_dir.glob("*.whl")
            if normalize_name(parse_wheel_name(p.name)[0]) == key
        )
        for req in read_requires_dist(whl_file):
            if req.startswith("nvidia-") and "nccl" in req:
                # lmdeploy 0.0.14 pins cu11; torch cu128 uses cu12 — optional at runtime
                continue
            dep = req_name(req)
            if dep in ("python", "pip"):
                continue
            walk(dep, depth + 1)

    targets = ["lmdeploy", "triton", "mineru", "fastapi", "uvicorn", "starlette", "anyio"]
    for t in targets:
        walk(t)

    print(f"Wheels dir: {wheels_dir.resolve()}")
    print(f"Total wheels: {len(list(wheels_dir.glob('*.whl')))}")
    print()
    if missing:
        print("MISSING packages (no wheel in directory):")
        for m in sorted(missing):
            print(f"  - {m}")
    else:
        print("All direct/transitive deps found as wheels.")

    sniff = find_wheel("sniffio")
    exc = find_wheel("exceptiongroup")
    print()
    print(f"sniffio wheel: {sniff or 'MISSING'}")
    print(f"exceptiongroup wheel: {exc or 'MISSING'}")
    lm = find_wheel("lmdeploy")
    print(f"lmdeploy wheel: {lm or 'MISSING'}")

    return 0 if not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())
