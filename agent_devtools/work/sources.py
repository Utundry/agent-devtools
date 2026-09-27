from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Iterable

from agent_devtools.core.io import atomic_json_write
from .knowledge import KNOWLEDGE_FORMAT, KNOWLEDGE_VERSION, KnowledgeError, load_records, validate_record
from .state import utc_now

class SourceError(RuntimeError):
    pass

def add_source(root: Path, *, url: str, title: str, claims: Iterable[str] = (), accessed_at: str | None = None) -> dict[str, Any]:
    url, title = url.strip(), title.strip()
    if not url or not title:
        raise SourceError("source url and title are required")
    record_id = uuid.uuid4().hex
    claim_list = [str(x).strip() for x in claims if str(x).strip()]
    record = {
        "format": KNOWLEDGE_FORMAT,
        "formatVersion": KNOWLEDGE_VERSION,
        "id": record_id,
        "kind": "source",
        "subject": f"source.{record_id}",
        "status": "active",
        "statement": title,
        "url": url,
        "title": title,
        "accessedAtUtc": (accessed_at or utc_now()).strip(),
        "claims": claim_list,
        "anchors": [],
        "supersedes": [],
        "changedFiles": [],
        "createdAtUtc": utc_now(),
    }
    try:
        record = validate_record(record)
    except KnowledgeError as exc:
        raise SourceError(str(exc)) from exc
    path = root / ".agent-knowledge" / "sources" / f"{record_id}.json"
    atomic_json_write(path, record)
    return record

def list_sources(root: Path) -> list[dict[str, Any]]:
    try:
        return [dict(x) for x in load_records(root) if x.get("kind") == "source"]
    except KnowledgeError as exc:
        raise SourceError(str(exc)) from exc
