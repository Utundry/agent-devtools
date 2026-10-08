from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

from agent_devtools.core.io import atomic_json_write
from agent_devtools.core.workspace import default_work_root
from .knowledge import effective_lifecycle_statuses, effective_statuses, explain, load_records
from .semantic_closeout import semantic_checkpoint
from .state import load_task_state, utc_now

PROJECTION_FORMAT = "agent-devtools-context-projection"
PROJECTION_VERSION = 1
DEFAULT_BUDGET = 1400
DEFAULT_DEDUP_THRESHOLD = 0.60

_STAGE_ALIASES = {
    "orient": "planning",
    "start": "planning",
    "align": "planning",
    "planning": "planning",
    "work": "implementation",
    "implementation": "implementation",
    "verify": "verification",
    "verification": "verification",
    "documentation": "documentation",
    "knowledge": "checkpointing",
    "checkpoint": "checkpointing",
    "checkpointing": "checkpointing",
    "handoff": "handoff",
    "finish": "operator_review",
    "operator-review": "operator_review",
    "operator_review": "operator_review",
}

def context_stage_choices() -> tuple[str, ...]:
    """Return every accepted public stage spelling in deterministic order."""
    return tuple(_STAGE_ALIASES)


_STAGE_KIND_WEIGHT = {
    "planning": {"requirement": 24, "open_question": 22, "decision": 20, "assumption": 16, "finding": 10, "evidence": 6, "source": 4},
    "implementation": {"requirement": 24, "decision": 24, "finding": 20, "assumption": 14, "evidence": 10, "open_question": 8, "source": 4},
    "verification": {"requirement": 22, "finding": 22, "evidence": 24, "decision": 18, "assumption": 12, "open_question": 8, "source": 6},
    "documentation": {"decision": 22, "requirement": 20, "finding": 18, "evidence": 14, "source": 12, "assumption": 8, "open_question": 6},
    "checkpointing": {"decision": 24, "requirement": 24, "finding": 20, "assumption": 18, "open_question": 16, "evidence": 14, "source": 8},
    "handoff": {"decision": 24, "requirement": 24, "finding": 22, "assumption": 20, "open_question": 20, "evidence": 18, "source": 8},
    "operator_review": {"requirement": 24, "decision": 24, "evidence": 22, "finding": 18, "assumption": 14, "open_question": 14, "source": 8},
}

_TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)


class ContextProjectionError(RuntimeError):
    pass


def _strings(values: Iterable[str] | None) -> list[str]:
    out: list[str] = []
    for raw in values or ():
        value = str(raw).strip()
        if value and value not in out:
            out.append(value)
    return out


def _tokens(value: str) -> set[str]:
    return {item.lower() for item in _TOKEN_RE.findall(value) if len(item) > 1}


def _scope_parts(value: str) -> tuple[str, ...]:
    value = str(value or "").strip().replace("\\", "/").strip("/").lower()
    return tuple(part for part in value.split("/") if part)


def _scope_relation(left: str, right: str) -> str | None:
    a, b = _scope_parts(left), _scope_parts(right)
    if not a or not b:
        return None
    if a == b:
        return "exact"
    if a == ("project",):
        return "ancestor"
    if b == ("project",):
        return "descendant"
    if len(a) < len(b) and b[: len(a)] == a:
        return "ancestor"
    if len(b) < len(a) and a[: len(b)] == b:
        return "descendant"
    return None


def _best_scope_match(record_scopes: list[str], query_scopes: list[str]) -> tuple[int, str | None]:
    best = 0
    reason = None
    weights = {"exact": 40, "ancestor": 30, "descendant": 26}
    for record_scope in record_scopes:
        for query_scope in query_scopes:
            relation = _scope_relation(record_scope, query_scope)
            score = weights.get(relation or "", 0)
            if score > best:
                best = score
                reason = f"scope:{relation}:{record_scope}->{query_scope}"
    return best, reason


def _normalize_stage(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "implementation"
    stage = _STAGE_ALIASES.get(raw)
    if stage is None:
        raise ContextProjectionError(
            "context stage must be planning, implementation, verification, documentation, checkpointing, handoff or operator_review"
        )
    return stage


def _latest_verification(root: Path) -> dict[str, Any] | None:
    try:
        from .verification import latest_verification
        return latest_verification(root)
    except Exception:
        return None


def derive_stage(root: Path, state: dict[str, Any] | None = None) -> str:
    state = state if state is not None else load_task_state(root)
    if not isinstance(state, dict):
        return "planning"
    alignment = state.get("taskAlignment") if isinstance(state.get("taskAlignment"), dict) else {}
    if alignment.get("status") != "ready":
        return "planning"
    if state.get("blockers"):
        return "implementation"
    try:
        closeout = semantic_checkpoint(root)
    except Exception:
        closeout = {"requiredPromotions": []}
    if closeout.get("requiredPromotions"):
        return "checkpointing"
    latest = _latest_verification(root)
    if latest:
        completed = str(latest.get("completedAtUtc") or "")
        started = str(state.get("startedAtUtc") or "")
        if completed and (not started or completed >= started) and str(latest.get("status") or "").lower() in {"pass", "passed", "success", "ok"}:
            return "operator_review"
    return "implementation"


def task_projection(root: Path, state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = state if state is not None else load_task_state(root)
    if not isinstance(state, dict):
        return {
            "currentObjective": None,
            "activeScope": [],
            "constraints": [],
            "definitionOfDone": [],
            "decisions": [],
            "assumptions": [],
            "openQuestions": [],
            "blockers": [],
            "nextAction": None,
            "projectionBasis": ["task-state"],
        }
    return {
        "taskId": state.get("taskId"),
        "originalObjective": state.get("goal"),
        "currentObjective": state.get("goal"),
        "activeScope": list(state.get("scope", [])),
        "constraints": list(state.get("constraints", [])),
        "definitionOfDone": list(state.get("definitionOfDone", [])),
        "requirements": list(state.get("requirements", [])),
        "decisions": list(state.get("decisions", [])),
        "findings": list(state.get("findings", [])),
        "assumptions": list(state.get("assumptions", [])),
        "openQuestions": list(state.get("openQuestions", [])),
        "evidence": list(state.get("evidence", [])),
        "blockers": list(state.get("blockers", [])),
        "nextAction": state.get("nextStep") or None,
        "framingChanges": [],
        "projectionBasis": ["task-state", "semantic-journal"],
    }


def operational_projection(root: Path, *, stage: str, state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = state if state is not None else load_task_state(root)
    required: list[str] = []
    blockers = list((state or {}).get("blockers", [])) if isinstance(state, dict) else []
    next_safe_action = "start or recover an explicit work item"
    if isinstance(state, dict):
        alignment = state.get("taskAlignment") if isinstance(state.get("taskAlignment"), dict) else {}
        if alignment.get("status") != "ready":
            required.append("resolve task alignment before substantial execution")
            next_safe_action = "resolve material task gaps or take the no-material-gaps fast path"
        elif blockers:
            required.append("resolve or explicitly retain blockers")
            next_safe_action = "resolve the first active blocker"
        else:
            try:
                closeout = semantic_checkpoint(root)
            except Exception:
                closeout = {"requiredPromotions": []}
            if closeout.get("requiredPromotions"):
                required.append("represent subject-bearing decisions/requirements in durable knowledge")
                next_safe_action = "review semantic checkpoint and promote required durable knowledge"
            else:
                latest = _latest_verification(root)
                completed = str((latest or {}).get("completedAtUtc") or "")
                started = str(state.get("startedAtUtc") or "")
                fresh_pass = bool(
                    latest
                    and str(latest.get("status") or "").lower() in {"pass", "passed", "success", "ok"}
                    and completed
                    and (not started or completed >= started)
                )
                if not fresh_pass:
                    required.append("collect current profile-appropriate verification evidence")
                    next_safe_action = "run or record the minimum sufficient verification"
                else:
                    next_safe_action = "complete the current work item"
    return {
        "stage": stage,
        "blockers": blockers,
        "requiredBeforeNextStage": required,
        "nextSafeAction": next_safe_action,
    }


def _record_scope(record: dict[str, Any]) -> list[str]:
    scope = _strings(record.get("scope", []))
    if not scope:
        subject = str(record.get("subject") or "").strip()
        if subject:
            scope = [subject]
    return scope


def _score_record(
    record: dict[str, Any],
    *,
    stage: str,
    query: str,
    query_scopes: list[str],
    paths: list[str],
    task_id: str,
) -> tuple[float, list[str], bool]:
    reasons: list[str] = []
    matched = not bool(query or query_scopes or paths)
    kind = str(record.get("kind") or "")
    score = float(_STAGE_KIND_WEIGHT.get(stage, {}).get(kind, 4))
    reasons.append(f"stage:{stage}:{kind}+{int(score)}")

    scope_score, scope_reason = _best_scope_match(_record_scope(record), query_scopes)
    if scope_score:
        score += scope_score
        reasons.append(scope_reason or f"scope+{scope_score}")
        matched = True

    haystack = " ".join(
        [
            str(record.get("subject") or ""),
            str(record.get("statement") or ""),
            " ".join(str(x) for x in record.get("anchors", [])),
            " ".join(str(x) for x in record.get("scope", [])),
        ]
    )
    query_tokens = _tokens(query)
    record_tokens = _tokens(haystack)
    overlap = sorted(query_tokens & record_tokens)
    session = record.get("sourceSession") if isinstance(record.get("sourceSession"), dict) else {}
    source_task_id = str(session.get("taskId") or "")
    source_goal_tokens = _tokens(str(session.get("taskGoal") or ""))
    same_task = bool(task_id and source_task_id and source_task_id == task_id)
    cross_task = bool(task_id and source_task_id and source_task_id != task_id)
    if overlap:
        term_score = min(30, len(overlap) * 6)
        score += term_score
        lexical_match = False
        if same_task:
            lexical_match = True
        elif cross_task:
            lexical_match = len(overlap) >= 2 and bool(query_tokens & source_goal_tokens)
        else:
            lexical_match = len(overlap) >= 2
        if lexical_match:
            reasons.append("terms:" + ",".join(overlap[:5]) + f"+{term_score}")
            if cross_task:
                reasons.append("cross-task-source-goal")
            matched = True
        else:
            reasons.append("terms:weak-cross-task-filtered")

    normalized_paths = [str(item).replace("\\", "/").lower() for item in paths]
    anchors = [str(item).replace("\\", "/").lower() for item in record.get("anchors", [])]
    scopes = [str(item).replace("\\", "/").lower() for item in _record_scope(record)]
    if any(any(anchor and (anchor in path or path in anchor) for anchor in anchors) for path in normalized_paths):
        score += 30
        reasons.append("path:anchor+30")
        matched = True
    elif any(any(scope and scope in path for scope in scopes) for path in normalized_paths):
        score += 20
        reasons.append("path:scope+20")
        matched = True

    if task_id and session.get("taskId") == task_id:
        score += 12
        reasons.append("same-task+12")
        matched = True

    confidence = str(record.get("confidence") or "")
    if confidence == "high":
        score += 4
        reasons.append("confidence:high+4")
    elif confidence == "medium":
        score += 2
        reasons.append("confidence:medium+2")

    return score, reasons, matched


def _jaccard(left: dict[str, Any], right: dict[str, Any]) -> float:
    a = _tokens(str(left.get("subject") or "") + " " + str(left.get("statement") or ""))
    b = _tokens(str(right.get("subject") or "") + " " + str(right.get("statement") or ""))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _dedupe(items: list[dict[str, Any]], threshold: float) -> tuple[list[dict[str, Any]], int]:
    kept: list[dict[str, Any]] = []
    omitted = 0
    for item in items:
        if any(_jaccard(item["record"], existing["record"]) >= threshold for existing in kept):
            omitted += 1
            continue
        kept.append(item)
    return kept, omitted


def _estimate_tokens(value: Any) -> int:
    return max(1, (len(json.dumps(value, ensure_ascii=False, sort_keys=True)) + 3) // 4)


def _cue(item: dict[str, Any], *, full: bool) -> dict[str, Any]:
    record = item["record"]
    base = {
        "id": record["id"],
        "kind": record["kind"],
        "subject": record["subject"],
        "score": round(float(item["score"]), 3),
        "reason": list(item["reason"]),
        "scope": list(record.get("scope", [])),
        "confidence": record.get("confidence"),
        "expandRef": f"knowledge:{record['id']}",
        "mode": "full" if full else "cue",
    }
    statement = str(record.get("statement") or "")
    if full:
        base["statement"] = statement
        base["evidenceRefs"] = list(record.get("evidenceRefs", []))
        base["sourceRefs"] = list(record.get("sourceRefs", []))
    else:
        base["summary"] = statement if len(statement) <= 180 else statement[:177].rstrip() + "..."
    return base


def usage_path(root: Path) -> Path:
    return default_work_root(root) / "context-usage.sqlite3"


def _record_usage(root: Path, *, task_id: str, stage: str, selected: list[dict[str, Any]]) -> str | None:
    if not selected:
        return None
    path = usage_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(sqlite3.connect(path)) as conn:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS knowledge_usage (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        knowledge_id TEXT NOT NULL,
                        task_id TEXT NOT NULL DEFAULT '',
                        stage TEXT NOT NULL,
                        score REAL NOT NULL,
                        used_at_utc TEXT NOT NULL
                    )
                    """
                )
                now = utc_now()
                conn.executemany(
                    "INSERT INTO knowledge_usage (knowledge_id, task_id, stage, score, used_at_utc) VALUES (?, ?, ?, ?, ?)",
                    [
                        (str(item["id"]), task_id, stage, float(item["score"]), now)
                        for item in selected
                    ],
                )
    except (sqlite3.Error, OSError) as exc:
        return f"knowledge usage tracking unavailable: {exc}"
    return None


def last_projection_path(root: Path) -> Path:
    return default_work_root(root) / "last-context-projection.json"


def prepare_context(
    root: Path,
    *,
    task: str | None = None,
    stage: str | None = None,
    scope: Iterable[str] = (),
    paths: Iterable[str] = (),
    budget: int = DEFAULT_BUDGET,
    dedup_threshold: float = DEFAULT_DEDUP_THRESHOLD,
    include_historical: bool = False,
) -> dict[str, Any]:
    if budget < 128:
        raise ContextProjectionError("context budget must be at least 128 approximate tokens")
    if not (0.0 <= dedup_threshold <= 1.0):
        raise ContextProjectionError("dedup threshold must be between 0 and 1")
    state = load_task_state(root)
    selected_stage = _normalize_stage(stage) if stage else derive_stage(root, state)
    task_info = task_projection(root, state)
    operational = operational_projection(root, stage=selected_stage, state=state)
    query = str(task or task_info.get("currentObjective") or "").strip()
    query_scopes = _strings([*task_info.get("activeScope", []), *scope])
    query_paths = _strings([*((state or {}).get("changedFiles", []) if isinstance(state, dict) else []), *paths])
    task_id = str((state or {}).get("taskId") or "") if isinstance(state, dict) else ""

    records = load_records(root)
    lifecycle = effective_lifecycle_statuses(records)
    semantic_status = effective_statuses(records)
    candidates: list[dict[str, Any]] = []
    lifecycle_omitted = 0
    semantic_omitted = 0
    relevance_omitted = 0
    for record in records:
        life = lifecycle.get(record["id"], "active")
        if life != "active" and not include_historical:
            lifecycle_omitted += 1
            continue
        status = semantic_status.get(record["id"], str(record.get("status") or ""))
        allowed_semantic = {
            "decision": {"active"},
            "requirement": {"active"},
            "finding": {"open", "confirmed"},
            "assumption": {"active", "confirmed"},
            "open_question": {"open"},
            "evidence": {"active"},
            "source": {"active"},
        }.get(str(record.get("kind") or ""), {status})
        if status not in allowed_semantic and not include_historical:
            semantic_omitted += 1
            continue
        score, reasons, matched = _score_record(
            record,
            stage=selected_stage,
            query=query,
            query_scopes=query_scopes,
            paths=query_paths,
            task_id=task_id,
        )
        if not matched:
            relevance_omitted += 1
            continue
        candidates.append({"record": record, "score": score, "reason": reasons, "lifecycle": life})
    candidates.sort(key=lambda item: (-float(item["score"]), str(item["record"]["id"])))
    deduped, duplicate_omitted = _dedupe(candidates, dedup_threshold)

    selected: list[dict[str, Any]] = []
    estimated = 0
    budget_omitted = 0
    for index, item in enumerate(deduped):
        record = item["record"]
        full = bool(
            index < 4
            and record.get("kind") in {"requirement", "decision"}
            and float(item["score"]) >= 35
        )
        projected = _cue(item, full=full)
        cost = _estimate_tokens(projected)
        if selected and estimated + cost > budget:
            budget_omitted += 1
            continue
        if not selected and cost > budget:
            projected = _cue(item, full=False)
            cost = _estimate_tokens(projected)
        if estimated + cost > budget:
            budget_omitted += 1
            continue
        selected.append(projected)
        estimated += cost

    payload = {
        "format": PROJECTION_FORMAT,
        "formatVersion": PROJECTION_VERSION,
        "task": task_info,
        "stage": selected_stage,
        "query": query,
        "scope": query_scopes,
        "paths": query_paths,
        "operational": operational,
        "budget": budget,
        "estimatedTokens": estimated,
        "candidateRecords": len(candidates),
        "selectedRecords": len(selected),
        "omitted": {
            "lifecycle": lifecycle_omitted,
            "semanticStatus": semantic_omitted,
            "irrelevant": relevance_omitted,
            "duplicateOverlap": duplicate_omitted,
            "contextBudget": budget_omitted,
        },
        "knowledge": selected,
        "canonicalDurableStore": ".agent-knowledge",
        "runtimeProjectionState": ".agent-work",
    }
    usage_warning = _record_usage(root, task_id=task_id, stage=selected_stage, selected=selected)
    if usage_warning:
        payload["runtimeWarnings"] = [usage_warning]
    atomic_json_write(last_projection_path(root), payload)
    return payload


def current_context(root: Path, *, stage: str | None = None) -> dict[str, Any]:
    state = load_task_state(root)
    selected_stage = _normalize_stage(stage) if stage else derive_stage(root, state)
    return {
        "format": "agent-devtools-current-task-projection",
        "formatVersion": 1,
        "task": task_projection(root, state),
        "operational": operational_projection(root, stage=selected_stage, state=state),
    }


def why_selected(root: Path, knowledge_id: str) -> dict[str, Any]:
    path = last_projection_path(root)
    if not path.is_file():
        raise ContextProjectionError("no context projection exists; run context prepare first")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextProjectionError(f"cannot read last context projection: {exc}") from exc
    item = next((row for row in payload.get("knowledge", []) if row.get("id") == knowledge_id), None)
    if item is None:
        raise ContextProjectionError(f"knowledge record was not selected by the last context projection: {knowledge_id}")
    return {
        "format": "agent-devtools-context-selection-explanation",
        "formatVersion": 1,
        "id": knowledge_id,
        "stage": payload.get("stage"),
        "query": payload.get("query"),
        "score": item.get("score"),
        "reason": list(item.get("reason", [])),
        "mode": item.get("mode"),
        "expandRef": item.get("expandRef"),
    }


def expand_context(root: Path, reference: str) -> dict[str, Any]:
    value = str(reference or "").strip()
    if value.startswith("knowledge:"):
        value = value.split(":", 1)[1]
    if not value:
        raise ContextProjectionError("context expand requires a knowledge id or knowledge:<id> reference")
    return explain(root, value)
