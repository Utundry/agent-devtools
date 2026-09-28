from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from agent_devtools.core.io import atomic_json_write
from .state import TaskStateError, load_task_state, utc_now

KNOWLEDGE_FORMAT = "agent-devtools-project-knowledge"
KNOWLEDGE_VERSION = 1
_ALLOWED_KINDS = {"decision", "finding", "assumption", "requirement", "open_question", "evidence", "source"}
_ALLOWED_STATUS = {
    "decision": {"active", "superseded"},
    "finding": {"open", "confirmed", "resolved", "invalidated"},
    "assumption": {"active", "confirmed", "rejected", "expired"},
    "requirement": {"active", "superseded"},
    "open_question": {"open", "resolved", "superseded"},
    "evidence": {"active", "superseded"},
    "source": {"active", "superseded"},
}


class KnowledgeError(RuntimeError):
    pass


def knowledge_root(root: Path) -> Path:
    return root / ".agent-knowledge"


def _safe_id(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return value or uuid.uuid4().hex


def _record_path(root: Path, kind: str, record_id: str) -> Path:
    return knowledge_root(root) / f"{kind}s" / f"{_safe_id(record_id)}.json"


def _strings(values: Iterable[str] | None) -> list[str]:
    out: list[str] = []
    for raw in values or ():
        value = str(raw).strip()
        if value and value not in out:
            out.append(value)
    return out


def validate_record(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise KnowledgeError("knowledge record must be a JSON object")
    if payload.get("format") != KNOWLEDGE_FORMAT or int(payload.get("formatVersion") or 0) != KNOWLEDGE_VERSION:
        raise KnowledgeError("unsupported knowledge record format/version")
    kind = str(payload.get("kind") or "").strip()
    if kind not in _ALLOWED_KINDS:
        raise KnowledgeError("knowledge kind must be decision, finding, assumption, requirement, open_question, evidence or source")
    statement = str(payload.get("statement") or "").strip()
    if not statement:
        raise KnowledgeError("knowledge statement is empty")
    record_id = str(payload.get("id") or "").strip()
    if not record_id:
        raise KnowledgeError("knowledge record id is empty")
    subject = str(payload.get("subject") or "").strip()
    if not subject:
        raise KnowledgeError("knowledge subject is empty")
    status = str(payload.get("status") or "").strip()
    if status not in _ALLOWED_STATUS[kind]:
        raise KnowledgeError(f"invalid {kind} knowledge status: {status}")
    clean = dict(payload)
    clean.update({"id": record_id, "kind": kind, "statement": statement, "subject": subject, "status": status})
    created_by = clean.get("createdBy")
    if created_by is not None:
        if not isinstance(created_by, dict):
            raise KnowledgeError("knowledge createdBy must be an object")
        normalized: dict[str, str] = {}
        for key in ("author", "agentEnvironment", "workstation"):
            value = str(created_by.get(key) or "").strip()
            if value:
                normalized[key] = value
        clean["createdBy"] = normalized
    for key in ("anchors", "supersedes", "changedFiles", "sourceRefs"):
        value = clean.get(key, [])
        if not isinstance(value, list):
            raise KnowledgeError(f"knowledge {key} must be an array")
        clean[key] = _strings(str(item) for item in value)
    if kind == "source":
        clean["url"] = str(clean.get("url") or "").strip()
        clean["title"] = str(clean.get("title") or "").strip()
        if not clean["url"] or not clean["title"]:
            raise KnowledgeError("source knowledge requires url and title")
        claims = clean.get("claims", [])
        if not isinstance(claims, list):
            raise KnowledgeError("source knowledge claims must be an array")
        clean["claims"] = _strings(str(item) for item in claims)
        clean["accessedAtUtc"] = str(clean.get("accessedAtUtc") or "").strip()
    return clean


def load_records(root: Path) -> list[dict[str, Any]]:
    base = knowledge_root(root)
    if not base.is_dir():
        return []
    records: list[dict[str, Any]] = []
    seen: dict[str, Path] = {}
    for path in sorted(base.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KnowledgeError(f"cannot read knowledge record {path.relative_to(root)}: {exc}") from exc
        record = validate_record(payload)
        if record["id"] in seen:
            raise KnowledgeError(f"duplicate knowledge id {record['id']!r}: {seen[record['id']]} and {path}")
        seen[record["id"]] = path
        records.append(record)
    return records


def _default_status(kind: str) -> str:
    return "open" if kind in {"finding", "open_question"} else "active"


def promote(
    root: Path,
    *,
    kind: str,
    text: str,
    subject: str,
    anchors: Iterable[str] = (),
    supersedes: Iterable[str] = (),
    author: str | None = None,
    agent_environment: str | None = None,
    workstation: str | None = None,
    source_refs: Iterable[str] = (),
) -> dict[str, Any]:
    kind = kind.strip()
    if kind not in _ALLOWED_KINDS:
        raise KnowledgeError("knowledge kind must be decision, finding, assumption, requirement, open_question, evidence or source")
    state = load_task_state(root)
    if state is None:
        raise KnowledgeError("no task state exists; durable promotion requires current task provenance")
    key = {"decision": "decisions", "finding": "findings", "assumption": "assumptions", "requirement": "requirements", "open_question": "openQuestions", "evidence": "evidence"}[kind]
    text = text.strip()
    if text not in state.get(key, []):
        raise KnowledgeError(f"cannot promote unrecorded session {kind}; record it with cognition first")
    subject = subject.strip()
    if not subject:
        raise KnowledgeError("knowledge subject is required")
    supersedes_ids = _strings(supersedes)
    existing = {item["id"]: item for item in load_records(root)}
    missing = [item for item in supersedes_ids if item not in existing]
    if missing:
        raise KnowledgeError("unknown supersedes knowledge id(s): " + ", ".join(missing))
    source_ids = _strings(source_refs)
    missing_sources = [item for item in source_ids if item not in existing or existing[item].get("kind") != "source"]
    if missing_sources:
        raise KnowledgeError("unknown source knowledge id(s): " + ", ".join(missing_sources))
    record_id = uuid.uuid4().hex
    now = utc_now()
    created_by = {
        key: value
        for key, value in (
            ("author", (author or os.getenv("AGENT_DEVTOOLS_AUTHOR", "")).strip()),
            ("agentEnvironment", (agent_environment or os.getenv("AGENT_DEVTOOLS_AGENT_ENVIRONMENT", "")).strip()),
            ("workstation", (workstation or os.getenv("AGENT_DEVTOOLS_WORKSTATION", "")).strip()),
        )
        if value
    }
    record = {
        "format": KNOWLEDGE_FORMAT,
        "formatVersion": KNOWLEDGE_VERSION,
        "id": record_id,
        "kind": kind,
        "subject": subject,
        "status": _default_status(kind),
        "statement": text,
        "anchors": _strings(anchors),
        "supersedes": supersedes_ids,
        "sourceRefs": source_ids,
        "taskId": state.get("taskId"),
        "taskGoal": state.get("goal"),
        "changedFiles": list(state.get("changedFiles", [])),
        "createdAtUtc": now,
    }
    if created_by:
        record["createdBy"] = created_by
    path = _record_path(root, kind, record_id)
    atomic_json_write(path, record)
    return record


def effective_statuses(records: list[dict[str, Any]]) -> dict[str, str]:
    superseded = {target for item in records for target in item.get("supersedes", [])}
    status: dict[str, str] = {}
    for item in records:
        value = item["status"]
        if item["kind"] in {"decision", "requirement", "open_question", "evidence", "source"} and item["id"] in superseded:
            value = "superseded"
        status[item["id"]] = value
    return status



def soft_contradictions(root: Path, *, kind: str, text: str, subject: str, supersedes: Iterable[str] = ()) -> list[dict[str, Any]]:
    subject = str(subject or "").strip()
    text = str(text or "").strip()
    if not subject or not text:
        return []
    records = load_records(root)
    statuses = effective_statuses(records)
    supersedes_ids = set(_strings(supersedes))
    warnings: list[dict[str, Any]] = []
    for item in records:
        if item.get("kind") == "source" or item.get("subject") != subject:
            continue
        effective = statuses.get(item["id"], item.get("status"))
        if effective not in {"active", "open", "confirmed"}:
            continue
        if item.get("statement") == text or item.get("id") in supersedes_ids:
            continue
        warnings.append({
            "recordId": item["id"],
            "kind": item["kind"],
            "subject": subject,
            "effectiveStatus": effective,
            "statement": item["statement"],
            "relation": "different-active-knowledge",
        })
    return warnings

def conflicts(root: Path) -> list[dict[str, Any]]:
    records = load_records(root)
    statuses = effective_statuses(records)
    active: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in records:
        if item["kind"] != "decision" or statuses[item["id"]] != "active":
            continue
        active.setdefault((item["kind"], item["subject"]), []).append(item)
    result: list[dict[str, Any]] = []
    for (_, subject), items in sorted(active.items()):
        statements = {item["statement"] for item in items}
        if len(items) > 1 and len(statements) > 1:
            result.append({
                "subject": subject,
                "recordIds": [item["id"] for item in items],
                "statements": [item["statement"] for item in items],
            })
    return result


def validate_knowledge(root: Path) -> dict[str, Any]:
    records = load_records(root)
    by_id = {item["id"]: item for item in records}
    dangling: list[dict[str, str]] = []
    for item in records:
        for target in item.get("supersedes", []):
            if target not in by_id:
                dangling.append({"recordId": item["id"], "missing": target})
    conflict_items = conflicts(root)
    return {
        "format": "agent-devtools-knowledge-validation",
        "formatVersion": 1,
        "records": len(records),
        "conflicts": conflict_items,
        "danglingSupersedes": dangling,
        "ok": not conflict_items and not dangling,
    }


def knowledge_status(root: Path) -> dict[str, Any]:
    records = load_records(root)
    statuses = effective_statuses(records)
    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for item in records:
        kind = str(item.get("kind") or "unknown")
        status = str(statuses.get(item["id"]) or item.get("status") or "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
    validation = validate_knowledge(root)
    return {
        "format": "agent-devtools-knowledge-status",
        "formatVersion": 1,
        "records": len(records),
        "byKind": dict(sorted(by_kind.items())),
        "byEffectiveStatus": dict(sorted(by_status.items())),
        "conflicts": validation["conflicts"],
        "danglingSupersedes": validation["danglingSupersedes"],
        "ok": validation["ok"],
    }


def knowledge_fingerprint(root: Path) -> str:
    from agent_devtools.core.hashing import stable_fingerprint
    records = sorted(load_records(root), key=lambda item: item["id"])
    return stable_fingerprint({"schema": "agent-devtools-project-knowledge-v1", "records": records})


def knowledge_snapshot(root: Path, *, release_version: str, source_fingerprint: str | None) -> dict[str, Any]:
    records = sorted(load_records(root), key=lambda item: item["id"])
    from agent_devtools.core.hashing import stable_fingerprint
    fingerprint = stable_fingerprint({"schema": "agent-devtools-project-knowledge-v1", "records": records})
    return {
        "format": "agent-devtools-knowledge-snapshot",
        "formatVersion": 1,
        "releaseVersion": release_version,
        "sourceFingerprint": source_fingerprint,
        "knowledgeFingerprint": fingerprint,
        "records": records,
        "recordCount": len(records),
    }


def verify_knowledge_snapshot(payload: Any) -> list[str]:
    from agent_devtools.core.hashing import stable_fingerprint
    if not isinstance(payload, dict) or payload.get("format") != "agent-devtools-knowledge-snapshot" or int(payload.get("formatVersion") or 0) != 1:
        return ["unsupported knowledge snapshot format/version"]
    records = payload.get("records")
    if not isinstance(records, list):
        return ["knowledge snapshot records must be an array"]
    clean: list[dict[str, Any]] = []
    failures: list[str] = []
    for index, item in enumerate(records):
        try:
            clean.append(validate_record(item))
        except KnowledgeError as exc:
            failures.append(f"invalid knowledge record {index}: {exc}")
    expected = stable_fingerprint({"schema": "agent-devtools-project-knowledge-v1", "records": sorted(clean, key=lambda item: item["id"])})
    if str(payload.get("knowledgeFingerprint") or "") != expected:
        failures.append("knowledge fingerprint mismatch")
    if int(payload.get("recordCount") or 0) != len(records):
        failures.append("knowledge record count mismatch")
    return failures
