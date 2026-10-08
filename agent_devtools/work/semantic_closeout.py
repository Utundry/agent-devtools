from __future__ import annotations

from pathlib import Path
from typing import Any

from .journal import SemanticJournalError, events_for_task
from .knowledge import effective_lifecycle_statuses, effective_statuses, load_records
from .state import TaskStateError, load_task_state

_KNOWLEDGE_KIND = {
    "decision": "decision",
    "finding": "finding",
    "assumption": "assumption",
    "requirement": "requirement",
    "question": "open_question",
    "evidence": "evidence",
}
_REQUIRED_DURABLE_KINDS = {"decision", "requirement"}
_ADVISORY_DURABLE_KINDS = {"finding", "assumption", "question"}


def _represented(
    event: dict[str, Any],
    records: list[dict[str, Any]],
    statuses: dict[str, str],
    lifecycle: dict[str, str],
) -> bool:
    target_kind = _KNOWLEDGE_KIND.get(str(event.get("kind") or ""))
    subject = str(event.get("subject") or "").strip()
    if not target_kind or not subject:
        return False
    for record in records:
        if record.get("kind") != target_kind:
            continue
        if str(record.get("subject") or "") != subject:
            continue
        if str(record.get("statement") or "") != str(event.get("text") or ""):
            continue
        record_id = str(record.get("id") or "")
        if lifecycle.get(record_id, "active") != "active":
            continue
        if statuses.get(record_id, str(record.get("status") or "")) in {
            "active", "open", "confirmed"
        }:
            return True
    return False


def semantic_checkpoint(root: Path) -> dict[str, Any]:
    state = load_task_state(root)
    if state is None:
        raise TaskStateError("no task state exists; use work start first")
    task_id = str(state.get("taskId") or "")
    try:
        events = events_for_task(root, task_id)
    except SemanticJournalError as exc:
        raise TaskStateError(str(exc)) from exc
    records = load_records(root)
    statuses = effective_statuses(records)
    lifecycle = effective_lifecycle_statuses(records)
    by_kind: dict[str, int] = {}
    required: list[dict[str, Any]] = []
    advisory: list[dict[str, Any]] = []
    for event in events:
        kind = str(event.get("kind") or "")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        subject = str(event.get("subject") or "").strip()
        if not subject or kind not in (_REQUIRED_DURABLE_KINDS | _ADVISORY_DURABLE_KINDS):
            continue
        if _represented(event, records, statuses, lifecycle):
            continue
        candidate = {
            "eventId": event.get("id"),
            "kind": kind,
            "subject": subject,
            "text": event.get("text"),
        }
        if kind in _REQUIRED_DURABLE_KINDS:
            required.append(candidate)
        else:
            advisory.append(candidate)
    return {
        "format": "agent-devtools-semantic-checkpoint",
        "formatVersion": 1,
        "taskId": task_id,
        "events": len(events),
        "byKind": dict(sorted(by_kind.items())),
        "requiredPromotions": required,
        "advisoryCandidates": advisory,
        "clean": not required,
        "policy": {
            "required": "subject-bearing decisions and requirements must have durable representation",
            "advisory": "subject-bearing findings, assumptions and questions are review candidates only",
            "subjectless": "session-local cognition does not create promotion bureaucracy",
        },
    }


def require_semantic_closeout(root: Path) -> dict[str, Any]:
    report = semantic_checkpoint(root)
    if report["requiredPromotions"]:
        missing = ", ".join(
            f"{item['kind']}:{item['subject']}" for item in report["requiredPromotions"][:5]
        )
        raise TaskStateError(
            "cannot complete work before semantic closeout; durable representation is missing for " + missing
        )
    return report
