from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

from agent_devtools.core.workspace import default_work_root

JOURNAL_FORMAT = "agent-devtools-semantic-journal"
JOURNAL_VERSION = 1
ALLOWED_KINDS = {
    "observation",
    "finding",
    "assumption",
    "decision",
    "requirement",
    "verification",
    "question",
    "evidence",
    "blocker",
}


class SemanticJournalError(RuntimeError):
    pass


def journal_path(root: Path) -> Path:
    return default_work_root(root) / "semantic-journal.sqlite3"


def _connect(root: Path) -> sqlite3.Connection:
    path = journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS semantic_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                task_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                subject TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at_utc TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_semantic_events_task_seq "
            "ON semantic_events(task_id, seq)"
        )
        return conn
    except BaseException:
        conn.close()
        raise


def _normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    kind = str(event.get("kind") or "").strip()
    text = str(event.get("text") or "").strip()
    subject = str(event.get("subject") or "").strip()
    if kind not in ALLOWED_KINDS:
        raise SemanticJournalError(f"unsupported semantic event kind: {kind}")
    if not text:
        raise SemanticJournalError("semantic event text is required")
    return {
        "id": uuid.uuid4().hex,
        "kind": kind,
        "text": text,
        "subject": subject,
        "metadata": dict(event.get("metadata") or {}),
    }


def append_events(
    root: Path,
    *,
    task_id: str,
    events: Iterable[dict[str, Any]],
    created_at_utc: str,
) -> list[dict[str, Any]]:
    task_id = str(task_id or "").strip()
    if not task_id:
        raise SemanticJournalError("semantic event task_id is required")
    normalized = [_normalize_event(dict(item)) for item in events]
    if not normalized:
        return []
    written: list[dict[str, Any]] = []
    try:
        with closing(_connect(root)) as conn:
            with conn:
                for event in normalized:
                    cursor = conn.execute(
                        """
                        INSERT INTO semantic_events
                            (id, task_id, kind, text, subject, metadata_json, created_at_utc)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event["id"],
                            task_id,
                            event["kind"],
                            event["text"],
                            event["subject"],
                            json.dumps(event["metadata"], ensure_ascii=False, sort_keys=True),
                            created_at_utc,
                        ),
                    )
                    written.append({
                        "format": JOURNAL_FORMAT,
                        "formatVersion": JOURNAL_VERSION,
                        "seq": int(cursor.lastrowid),
                        "id": event["id"],
                        "taskId": task_id,
                        "kind": event["kind"],
                        "text": event["text"],
                        "subject": event["subject"],
                        "metadata": event["metadata"],
                        "createdAtUtc": created_at_utc,
                    })
    except (sqlite3.Error, OSError) as exc:
        raise SemanticJournalError(f"cannot append semantic event batch: {exc}") from exc
    return written


def append_event(
    root: Path,
    *,
    task_id: str,
    kind: str,
    text: str,
    created_at_utc: str,
    subject: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return append_events(
        root,
        task_id=task_id,
        events=[{"kind": kind, "text": text, "subject": subject, "metadata": metadata or {}}],
        created_at_utc=created_at_utc,
    )[0]


def events_for_task(root: Path, task_id: str) -> list[dict[str, Any]]:
    path = journal_path(root)
    if not path.is_file():
        return []
    try:
        with closing(_connect(root)) as conn:
            rows = conn.execute(
                "SELECT seq, id, task_id, kind, text, subject, metadata_json, created_at_utc "
                "FROM semantic_events WHERE task_id = ? ORDER BY seq",
                (str(task_id),),
            ).fetchall()
    except (sqlite3.Error, OSError) as exc:
        raise SemanticJournalError(f"cannot read semantic journal: {exc}") from exc
    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {"invalidMetadata": True}
        events.append({
            "seq": int(row["seq"]),
            "id": row["id"],
            "taskId": row["task_id"],
            "kind": row["kind"],
            "text": row["text"],
            "subject": row["subject"],
            "metadata": metadata,
            "createdAtUtc": row["created_at_utc"],
        })
    return events


def possible_stale_cognition(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    prior=[]; out=[]
    def related(left: str, right: str) -> bool:
        if left == right: return True
        return any(right.startswith(left + sep) or left.startswith(right + sep) for sep in ("-", "/", ".", ":"))
    for event in events:
        kind=str(event.get("kind") or ""); subject=str(event.get("subject") or "").strip(); text=str(event.get("text") or "").strip()
        if kind not in {"assumption", "question"} or not subject or not text: continue
        match=None
        for older in reversed(prior):
            if older["kind"] == kind and older["text"] != text and related(str(older["subject"]), subject): match=older; break
        if match is not None:
            out.append({"kind":kind,"olderEventId":match.get("id"),"newerEventId":event.get("id"),"olderSubject":match.get("subject"),"newerSubject":subject,"olderText":match.get("text"),"newerText":text})
        prior.append({"id":event.get("id"),"kind":kind,"subject":subject,"text":text})
    return out


def journal_status(root: Path, task_id: str) -> dict[str, Any]:
    events = events_for_task(root, task_id)
    by_kind: dict[str, int] = {}
    for item in events:
        kind = str(item["kind"])
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {
        "format": JOURNAL_FORMAT,
        "formatVersion": JOURNAL_VERSION,
        "taskId": task_id,
        "events": len(events),
        "byKind": dict(sorted(by_kind.items())),
        "lastEvent": events[-1] if events else None,
    }
