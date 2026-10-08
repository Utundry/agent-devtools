from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Iterable

from agent_devtools.core.io import atomic_json_write
from .state import load_task_state, utc_now

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
_ALLOWED_LIFECYCLE = {"active", "superseded", "historical", "rejected"}
_ALLOWED_CONFIDENCE = {"low", "medium", "high"}
_EVENT_TO_KNOWLEDGE_KIND = {
    "decision": "decision",
    "finding": "finding",
    "assumption": "assumption",
    "requirement": "requirement",
    "question": "open_question",
    "evidence": "evidence",
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


def _clean_source_session(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise KnowledgeError("knowledge sourceSession must be an object")
    clean: dict[str, str] = {}
    for key in ("taskId", "taskGoal", "eventId"):
        item = str(value.get(key) or "").strip()
        if item:
            clean[key] = item
    return clean


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
    lifecycle = str(payload.get("lifecycleStatus") or "active").strip()
    if lifecycle not in _ALLOWED_LIFECYCLE:
        raise KnowledgeError(f"invalid knowledge lifecycle status: {lifecycle}")
    confidence_raw = payload.get("confidence")
    confidence = None if confidence_raw in {None, ""} else str(confidence_raw).strip().lower()
    if confidence is not None and confidence not in _ALLOWED_CONFIDENCE:
        raise KnowledgeError("knowledge confidence must be low, medium or high")
    clean = dict(payload)
    clean.update({
        "id": record_id,
        "kind": kind,
        "statement": statement,
        "subject": subject,
        "status": status,
    })
    # Lifecycle fields are additive. Do not inject absent defaults into legacy v1
    # records: historical knowledge snapshots fingerprint the normalized record
    # shape, so upgrading the runtime must not invalidate old snapshot evidence.
    if "lifecycleStatus" in clean:
        clean["lifecycleStatus"] = lifecycle
    if "confidence" in clean:
        clean["confidence"] = confidence
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
    for key in ("scope", "evidenceRefs"):
        if key not in clean:
            continue
        value = clean.get(key)
        if not isinstance(value, list):
            raise KnowledgeError(f"knowledge {key} must be an array")
        clean[key] = _strings(str(item) for item in value)
    if "sourceSession" in clean:
        clean["sourceSession"] = _clean_source_session(clean.get("sourceSession"))
    if "updatedAtUtc" in clean:
        clean["updatedAtUtc"] = str(clean.get("updatedAtUtc") or "").strip()
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


def effective_statuses(records: list[dict[str, Any]]) -> dict[str, str]:
    """Backward-compatible semantic status projection."""
    superseded = {target for item in records for target in item.get("supersedes", [])}
    status: dict[str, str] = {}
    for item in records:
        value = item["status"]
        if item["kind"] in {"decision", "requirement", "open_question", "evidence", "source"} and item["id"] in superseded:
            value = "superseded"
        status[item["id"]] = value
    return status


def effective_lifecycle_statuses(records: list[dict[str, Any]]) -> dict[str, str]:
    """Project-knowledge lifecycle; supersession is relation-derived and append-friendly."""
    superseded = {target for item in records for target in item.get("supersedes", [])}
    result: dict[str, str] = {}
    for item in records:
        value = str(item.get("lifecycleStatus") or "active")
        if value == "active" and item["id"] in superseded:
            value = "superseded"
        result[item["id"]] = value
    return result


def _current_event(root: Path, *, task_id: str, kind: str, text: str, subject: str) -> dict[str, Any] | None:
    try:
        from .journal import events_for_task
        events = events_for_task(root, task_id)
    except Exception:
        return None
    event_kind = "question" if kind == "open_question" else kind
    for item in reversed(events):
        if item.get("kind") != event_kind:
            continue
        if str(item.get("text") or "") != text:
            continue
        if str(item.get("subject") or "") != subject:
            continue
        return item
    return None


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
    scope: Iterable[str] = (),
    confidence: str | None = None,
    evidence_refs: Iterable[str] = (),
) -> dict[str, Any]:
    kind = kind.strip()
    if kind not in _ALLOWED_KINDS or kind == "source":
        raise KnowledgeError("knowledge kind must be decision, finding, assumption, requirement, open_question or evidence")
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
    confidence_value = None if confidence in {None, ""} else str(confidence).strip().lower()
    if confidence_value is not None and confidence_value not in _ALLOWED_CONFIDENCE:
        raise KnowledgeError("knowledge confidence must be low, medium or high")
    supersedes_ids = _strings(supersedes)
    existing_records = load_records(root)
    existing = {item["id"]: item for item in existing_records}
    missing = [item for item in supersedes_ids if item not in existing]
    if missing:
        raise KnowledgeError("unknown supersedes knowledge id(s): " + ", ".join(missing))
    for target in supersedes_ids:
        older = existing[target]
        if older.get("kind") != kind or older.get("subject") != subject:
            raise KnowledgeError("supersedes relation requires the same knowledge kind and subject")
    source_ids = _strings(source_refs)
    missing_sources = [item for item in source_ids if item not in existing or existing[item].get("kind") != "source"]
    if missing_sources:
        raise KnowledgeError("unknown source knowledge id(s): " + ", ".join(missing_sources))
    anchor_values = _strings(anchors)
    scope_values = _strings(scope) or _strings(state.get("scope", []))
    evidence_values = _strings(evidence_refs)
    lifecycle = effective_lifecycle_statuses(existing_records)
    if not supersedes_ids:
        for item in existing_records:
            if (lifecycle.get(item["id"], "active") == "active"
                    and item["status"] == _default_status(kind)
                    and item["kind"] == kind and item["subject"] == subject
                    and item["statement"] == text
                    and item.get("anchors", []) == anchor_values
                    and item.get("sourceRefs", []) == source_ids
                    and item.get("scope", []) == scope_values
                    and item.get("evidenceRefs", []) == evidence_values
                    and item.get("confidence") == confidence_value):
                return item
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
    event = _current_event(root, task_id=str(state.get("taskId") or ""), kind=kind, text=text, subject=subject)
    source_session = {
        key: value for key, value in (
            ("taskId", str(state.get("taskId") or "")),
            ("taskGoal", str(state.get("goal") or "")),
            ("eventId", str((event or {}).get("id") or "")),
        ) if value
    }
    record = {
        "format": KNOWLEDGE_FORMAT,
        "formatVersion": KNOWLEDGE_VERSION,
        "id": record_id,
        "kind": kind,
        "subject": subject,
        "status": _default_status(kind),
        "lifecycleStatus": "active",
        "statement": text,
        "scope": scope_values,
        "confidence": confidence_value,
        "evidenceRefs": evidence_values,
        "anchors": anchor_values,
        "supersedes": supersedes_ids,
        "sourceRefs": source_ids,
        "sourceSession": source_session,
        "taskId": state.get("taskId"),
        "taskGoal": state.get("goal"),
        "changedFiles": list(state.get("changedFiles", [])),
        "createdAtUtc": now,
        "updatedAtUtc": now,
    }
    if created_by:
        record["createdBy"] = created_by
    path = _record_path(root, kind, record_id)
    atomic_json_write(path, record)
    return validate_record(record)


def remember(
    root: Path,
    *,
    event_id: str,
    scope: Iterable[str] = (),
    confidence: str | None = None,
    evidence_refs: Iterable[str] = (),
) -> dict[str, Any]:
    state = load_task_state(root)
    if state is None:
        raise KnowledgeError("no task state exists; remember requires current task provenance")
    try:
        from .journal import events_for_task
        events = events_for_task(root, str(state.get("taskId") or ""))
    except Exception as exc:
        raise KnowledgeError(f"cannot read semantic journal for remember: {exc}") from exc
    event = next((item for item in events if item.get("id") == event_id), None)
    if event is None:
        raise KnowledgeError(f"semantic event not found in current task: {event_id}")
    kind = _EVENT_TO_KNOWLEDGE_KIND.get(str(event.get("kind") or ""))
    if not kind:
        raise KnowledgeError("semantic event kind is not promotable to durable knowledge")
    subject = str(event.get("subject") or "").strip()
    if not subject:
        raise KnowledgeError("cannot remember subjectless session-local cognition")
    return promote(
        root,
        kind=kind,
        text=str(event.get("text") or ""),
        subject=subject,
        scope=scope,
        confidence=confidence,
        evidence_refs=evidence_refs,
    )


def set_lifecycle(root: Path, *, record_id: str, lifecycle_status: str) -> dict[str, Any]:
    lifecycle_status = lifecycle_status.strip().lower()
    if lifecycle_status not in {"active", "historical", "rejected"}:
        raise KnowledgeError("explicit lifecycle status must be active, historical or rejected; superseded is relation-derived")
    records = load_records(root)
    record = next((item for item in records if item["id"] == record_id), None)
    if record is None:
        raise KnowledgeError(f"knowledge record not found: {record_id}")
    record = dict(record)
    record["lifecycleStatus"] = lifecycle_status
    record["updatedAtUtc"] = utc_now()
    atomic_json_write(_record_path(root, record["kind"], record["id"]), record)
    return validate_record(record)


def _supersession_cycles(records: list[dict[str, Any]]) -> list[list[str]]:
    graph = {item["id"]: list(item.get("supersedes", [])) for item in records}
    cycles: list[list[str]] = []
    visiting: list[str] = []
    active: set[str] = set()
    done: set[str] = set()

    def visit(node: str) -> None:
        if node in done:
            return
        if node in active:
            try:
                start = visiting.index(node)
            except ValueError:
                start = 0
            cycle = visiting[start:] + [node]
            if cycle not in cycles:
                cycles.append(cycle)
            return
        active.add(node)
        visiting.append(node)
        for target in graph.get(node, []):
            if target in graph:
                visit(target)
        visiting.pop()
        active.remove(node)
        done.add(node)

    for node in graph:
        visit(node)
    return cycles


def supersede(root: Path, *, older_id: str, newer_id: str) -> dict[str, Any]:
    if older_id == newer_id:
        raise KnowledgeError("knowledge record cannot supersede itself")
    records = load_records(root)
    by_id = {item["id"]: item for item in records}
    if older_id not in by_id or newer_id not in by_id:
        missing = [item for item in (older_id, newer_id) if item not in by_id]
        raise KnowledgeError("knowledge record not found: " + ", ".join(missing))
    older = by_id[older_id]
    newer = dict(by_id[newer_id])
    if older["kind"] != newer["kind"] or older["subject"] != newer["subject"]:
        raise KnowledgeError("supersedes relation requires the same knowledge kind and subject")
    if effective_lifecycle_statuses(records).get(newer_id) != "active":
        raise KnowledgeError("superseding record must be active")
    newer["supersedes"] = _strings([*newer.get("supersedes", []), older_id])
    candidate_records = [newer if item["id"] == newer_id else item for item in records]
    if _supersession_cycles(candidate_records):
        raise KnowledgeError("supersedes relation would create a cycle")
    newer["updatedAtUtc"] = utc_now()
    atomic_json_write(_record_path(root, newer["kind"], newer["id"]), newer)
    return validate_record(newer)


def explain(root: Path, record_id: str) -> dict[str, Any]:
    records = load_records(root)
    by_id = {item["id"]: item for item in records}
    record = by_id.get(record_id)
    if record is None:
        raise KnowledgeError(f"knowledge record not found: {record_id}")
    lifecycle = effective_lifecycle_statuses(records)
    superseded_by = [item["id"] for item in records if record_id in item.get("supersedes", [])]
    source_event = None
    session = record.get("sourceSession") or {}
    task_id = str(session.get("taskId") or "")
    event_id = str(session.get("eventId") or "")
    if task_id and event_id:
        try:
            from .journal import events_for_task
            source_event = next((item for item in events_for_task(root, task_id) if item.get("id") == event_id), None)
        except Exception:
            source_event = None
    return {
        "format": "agent-devtools-knowledge-explanation",
        "formatVersion": 1,
        "record": record,
        "effectiveLifecycleStatus": lifecycle[record_id],
        "supersedes": list(record.get("supersedes", [])),
        "supersededBy": superseded_by,
        "sourceSession": dict(session),
        "sourceEvent": source_event,
        "evidenceRefs": list(record.get("evidenceRefs", [])),
        "sourceRefs": list(record.get("sourceRefs", [])),
        "scope": list(record.get("scope", [])),
        "confidence": record.get("confidence"),
    }


def soft_contradictions(root: Path, *, kind: str, text: str, subject: str, supersedes: Iterable[str] = ()) -> list[dict[str, Any]]:
    subject = str(subject or "").strip()
    text = str(text or "").strip()
    if not subject or not text:
        return []
    records = load_records(root)
    statuses = effective_statuses(records)
    lifecycle = effective_lifecycle_statuses(records)
    supersedes_ids = set(_strings(supersedes))
    warnings: list[dict[str, Any]] = []
    for item in records:
        if item.get("kind") == "source" or item.get("subject") != subject:
            continue
        if lifecycle.get(item["id"]) != "active":
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
            "effectiveLifecycleStatus": lifecycle.get(item["id"]),
            "statement": item["statement"],
            "relation": "different-active-knowledge",
        })
    return warnings


def conflicts(root: Path) -> list[dict[str, Any]]:
    records = load_records(root)
    statuses = effective_statuses(records)
    lifecycle = effective_lifecycle_statuses(records)
    active: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in records:
        if item["kind"] != "decision" or statuses[item["id"]] != "active" or lifecycle[item["id"]] != "active":
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
    invalid_relations: list[dict[str, str]] = []
    for item in records:
        for target in item.get("supersedes", []):
            if target not in by_id:
                dangling.append({"recordId": item["id"], "missing": target})
                continue
            older = by_id[target]
            if older["kind"] != item["kind"] or older["subject"] != item["subject"]:
                invalid_relations.append({"recordId": item["id"], "target": target, "reason": "kind-or-subject-mismatch"})
    conflict_items = conflicts(root)
    cycles = _supersession_cycles(records)
    return {
        "format": "agent-devtools-knowledge-validation",
        "formatVersion": 2,
        "records": len(records),
        "conflicts": conflict_items,
        "danglingSupersedes": dangling,
        "invalidSupersedes": invalid_relations,
        "supersessionCycles": cycles,
        "ok": not conflict_items and not dangling and not invalid_relations and not cycles,
    }


def knowledge_status(root: Path) -> dict[str, Any]:
    records = load_records(root)
    statuses = effective_statuses(records)
    lifecycle = effective_lifecycle_statuses(records)
    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_lifecycle: dict[str, int] = {}
    for item in records:
        kind = str(item.get("kind") or "unknown")
        status = str(statuses.get(item["id"]) or item.get("status") or "unknown")
        life = str(lifecycle.get(item["id"]) or "active")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
        by_lifecycle[life] = by_lifecycle.get(life, 0) + 1
    validation = validate_knowledge(root)
    return {
        "format": "agent-devtools-knowledge-status",
        "formatVersion": 2,
        "records": len(records),
        "canonicalStore": ".agent-knowledge",
        "sqliteDurableStore": False,
        "byKind": dict(sorted(by_kind.items())),
        "byEffectiveStatus": dict(sorted(by_status.items())),
        "byLifecycleStatus": dict(sorted(by_lifecycle.items())),
        "conflicts": validation["conflicts"],
        "danglingSupersedes": validation["danglingSupersedes"],
        "invalidSupersedes": validation["invalidSupersedes"],
        "supersessionCycles": validation["supersessionCycles"],
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
