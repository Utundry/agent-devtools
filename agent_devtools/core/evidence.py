from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .hashing import stable_fingerprint
from .io import atomic_json_write

EVIDENCE_FORMAT = "agent-devtools-certified-evidence"
EVIDENCE_VERSION = 1
MAX_EVIDENCE_ENTRIES = 4096


class CertifiedEvidenceStore:
    """Content-addressed PASS evidence promoted only after an explicit successful flush."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, dict[str, Any]] = {}
        self.updates: dict[str, dict[str, Any]] = {}
        self.state = "missing"
        self.error: str | None = None
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("format") != EVIDENCE_FORMAT
                or int(payload.get("formatVersion") or 0) != EVIDENCE_VERSION
                or not isinstance(payload.get("entries"), dict)
            ):
                raise ValueError("unsupported evidence format/version")
            self.entries = {
                str(key): dict(value)
                for key, value in payload["entries"].items()
                if isinstance(value, dict)
            }
            self.state = "ready"
        except Exception as exc:
            self.entries = {}
            self.state = "corrupt"
            self.error = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def key(kind: str, item_id: str, input_fingerprint: str) -> str:
        return stable_fingerprint({"kind": kind, "id": item_id, "inputFingerprint": input_fingerprint})

    def probe(self, kind: str, item_id: str, input_fingerprint: str) -> dict[str, Any] | None:
        entry = self.entries.get(self.key(kind, item_id, input_fingerprint))
        if not isinstance(entry, dict):
            return None
        if (
            entry.get("kind") != kind
            or entry.get("id") != item_id
            or entry.get("inputFingerprint") != input_fingerprint
            or entry.get("status") != "pass"
        ):
            return None
        return dict(entry)

    def queue(
        self,
        *,
        kind: str,
        item_id: str,
        input_fingerprint: str,
        source_fingerprint: str,
        completed_at_utc: str,
        result: dict[str, Any],
        duration_seconds: float = 0.0,
    ) -> None:
        key = self.key(kind, item_id, input_fingerprint)
        self.updates[key] = {
            "kind": kind,
            "id": item_id,
            "inputFingerprint": input_fingerprint,
            "sourceFingerprint": source_fingerprint,
            "status": "pass",
            "completedAtUtc": completed_at_utc,
            "durationSeconds": round(float(duration_seconds), 6),
            "result": result,
        }

    def flush(self) -> None:
        if not self.updates:
            return
        merged = dict(self.entries)
        merged.update(self.updates)
        if len(merged) > MAX_EVIDENCE_ENTRIES:
            merged = dict(
                sorted(
                    merged.items(),
                    key=lambda pair: str((pair[1] or {}).get("completedAtUtc") or ""),
                    reverse=True,
                )[:MAX_EVIDENCE_ENTRIES]
            )
        atomic_json_write(
            self.path,
            {"format": EVIDENCE_FORMAT, "formatVersion": EVIDENCE_VERSION, "entries": merged},
        )
        self.entries = merged
        self.updates = {}
        self.state = "ready"
