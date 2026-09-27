from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_fingerprint(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def fingerprint_paths(root: Path, paths: Iterable[Path]) -> str:
    entries: list[tuple[str, str]] = []
    root = root.resolve()
    for path in paths:
        resolved = path.resolve()
        try:
            name = resolved.relative_to(root).as_posix()
        except ValueError:
            name = str(resolved)
        entries.append((name, sha256_file(resolved) if resolved.is_file() else "MISSING"))
    return stable_fingerprint(entries)
