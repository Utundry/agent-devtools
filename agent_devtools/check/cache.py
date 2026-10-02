from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from agent_devtools.core.files import iter_paths
from agent_devtools.core.pathmatch import matches_any
from agent_devtools.core.hashing import sha256_file, stable_fingerprint
from agent_devtools.core.identity import engine_fingerprint
from agent_devtools.core.io import atomic_json_write
from agent_devtools.core.tools import tool_identity

CACHE_FORMAT = "agent-devtools-check-cache"
CACHE_VERSION = 1
MAX_CACHE_ENTRIES = 512


def matching_input_hashes(root: Path, patterns: tuple[str, ...], ignore: tuple[str, ...]) -> dict[str, str]:
    return dict(sorted((path.relative_to(root).as_posix(), sha256_file(path))
                       for path in iter_paths(root, patterns, exclude=ignore)
                       if not path.is_symlink()))


class StageCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, dict[str, Any]] = {}
        self.updates: dict[str, dict[str, Any]] = {}
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("format") != CACHE_FORMAT
                or int(payload.get("formatVersion") or 0) != CACHE_VERSION
                or not isinstance(payload.get("entries"), dict)
            ):
                return
            self.entries = {str(k): dict(v) for k, v in payload["entries"].items() if isinstance(v, dict)}
        except Exception:
            self.entries = {}

    @staticmethod
    def key(
        *,
        suite: str,
        argv: tuple[str, ...],
        tool: str | None,
        input_hashes: dict[str, str],
        execution: dict[str, Any] | None = None,
    ) -> str:
        return stable_fingerprint({
            "cacheVersion": CACHE_VERSION,
            "engineFingerprint": engine_fingerprint(),
            "suite": suite,
            "argv": list(argv),
            "tool": tool_identity(tool) if tool else None,
            "inputs": input_hashes,
            "execution": execution or {},
        })

    def probe(self, key: str, suite: str) -> dict[str, Any] | None:
        entry = self.entries.get(key)
        if not isinstance(entry, dict) or entry.get("suite") != suite or entry.get("status") != "pass":
            return None
        return dict(entry)

    def queue(self, key: str, *, suite: str, result: dict[str, Any], completed_at_utc: str) -> None:
        self.updates[key] = {
            "suite": suite,
            "status": "pass",
            "completedAtUtc": completed_at_utc,
            "result": result,
        }

    def flush(self) -> None:
        if not self.updates:
            return
        merged = dict(self.entries)
        merged.update(self.updates)
        if len(merged) > MAX_CACHE_ENTRIES:
            merged = dict(
                sorted(
                    merged.items(),
                    key=lambda pair: str((pair[1] or {}).get("completedAtUtc") or ""),
                    reverse=True,
                )[:MAX_CACHE_ENTRIES]
            )
        atomic_json_write(self.path, {"format": CACHE_FORMAT, "formatVersion": CACHE_VERSION, "entries": merged})
        self.entries = merged
        self.updates = {}
