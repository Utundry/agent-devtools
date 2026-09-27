#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    manifest = root / "MANIFEST.sha256"
    failures: list[str] = []
    expected_files: set[str] = set()
    checked = 0
    if not manifest.is_file():
        print("FAIL")
        print("missing: MANIFEST.sha256")
        return 1
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            expected, rel = raw.split("  ", 1)
        except ValueError:
            print("FAIL")
            print(f"malformed MANIFEST.sha256 line: {raw}")
            return 1
        expected_files.add(rel)
        path = root / rel
        checked += 1
        if not path.is_file():
            failures.append(f"missing: {rel}")
        elif sha256(path) != expected:
            failures.append(f"hash mismatch: {rel}")
    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "MANIFEST.sha256"
    }
    for rel in sorted(actual_files - expected_files):
        failures.append(f"unexpected: {rel}")
    if failures:
        print("FAIL")
        for item in failures:
            print(item)
        return 1
    print(f"OK: {checked} files match MANIFEST.sha256")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
