"""Bounded, reproducible source snapshot for non-Git workspaces.

This is content evidence, not semantic change detection. A clean ZIP extraction
may be verified without Git; affected test selection remains unknown.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from agent_devtools.core.hashing import stable_fingerprint

_SOURCE_SUFFIXES = {".py", ".php", ".js", ".jsx", ".ts", ".tsx", ".vue", ".css", ".scss", ".html", ".sql", ".json", ".md", ".sh", ".toml", ".yaml", ".yml", ".xml", ".gcode"}
_IGNORE_DIRS = {".git", ".agent-work", ".agent-knowledge", ".agent-updates", "node_modules", "vendor", "dist", "build", "coverage", "__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache"}
_MAX_FILES = 12000
_MAX_BYTES = 512 * 1024 * 1024


def source_snapshot(root: Path) -> dict:
    """Fail closed when bounded collection cannot describe the complete source set."""
    root = root.resolve()
    records = []
    total = 0
    try:
        import os
        for folder, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in _IGNORE_DIRS and not (Path(folder) / d).is_symlink())
            for filename in sorted(files):
                path = Path(folder) / filename
                if path.is_symlink() or path.suffix.lower() not in _SOURCE_SUFFIXES:
                    continue
                relative = path.relative_to(root).as_posix()
                if len(records) >= _MAX_FILES:
                    return {"available": False, "reason": "source-snapshot-file-limit"}
                size = path.stat().st_size
                total += size
                if total > _MAX_BYTES:
                    return {"available": False, "reason": "source-snapshot-byte-limit"}
                h = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        h.update(chunk)
                records.append({"path": relative, "sha256": h.hexdigest()})
    except OSError as exc:
        return {"available": False, "reason": f"source-snapshot-io-error: {exc}"}
    if not records:
        return {"available": False, "reason": "source-snapshot-no-source-files"}
    return {"available": True, "mode": "directory-snapshot", "gitHead": "", "changedFiles": [],
            "fileCount": len(records), "files": records,
            "fingerprint": stable_fingerprint({"mode": "directory-snapshot", "files": records})}
