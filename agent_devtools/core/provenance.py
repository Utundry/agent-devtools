from __future__ import annotations

from pathlib import Path

from .hashing import sha256_file


def seal_origins(entries: dict, root: Path, report_path: Path) -> None:
    origin = {"report": report_path.relative_to(root).as_posix(), "reportSha256": sha256_file(report_path)}
    for entry in entries.values():
        entry["executionEvidence"] = dict(origin)


def origin_status(root: Path, entry: dict) -> dict:
    raw = entry.get("executionEvidence")
    if raw is not None and not isinstance(raw, dict):
        return {"detailsAvailable": False, "integrityMismatch": True}
    origin = dict(raw or {})
    if not origin.get("report"):
        return {"detailsAvailable": False}
    try:
        path = (root / origin["report"]).resolve()
        path.relative_to(root.resolve())
        exists = path.is_file()
        valid = not exists or sha256_file(path) == origin.get("reportSha256")
    except (OSError, TypeError, ValueError):
        return {**origin, "detailsAvailable": False, "integrityMismatch": True}
    return {**origin, "detailsAvailable": exists and valid, "integrityMismatch": not valid}
