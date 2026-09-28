from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from agent_devtools.handoff import HandoffError, resume_handoff

from .brief import build_brief
from .state import (
    TaskStateError,
    align_task,
    load_task_state,
    start_task,
    task_alignment_ready,
)


class WorkEntryError(TaskStateError):
    pass


def _values(items: Iterable[str]) -> list[str]:
    return [str(item).strip() for item in items if str(item).strip()]


def _fast_align_if_requested(root: Path, state: dict[str, Any], requested: bool) -> dict[str, Any]:
    if not requested:
        return state
    alignment = state.get("taskAlignment") if isinstance(state.get("taskAlignment"), dict) else {}
    status = str(alignment.get("status") or "pending")
    if status == "ready":
        return state
    if status == "clarification-required":
        raise WorkEntryError(
            "cannot replace previously identified material gaps with --no-material-gaps; "
            "resolve them through work align --user-approved"
        )
    return align_task(
        root,
        no_material_gaps=True,
        resolution="Work entry routine fast path: no material task gaps declared",
    )


def enter_work(
    root: Path,
    *,
    goal: str | None = None,
    scope: Iterable[str] = (),
    constraints: Iterable[str] = (),
    definition_of_done: Iterable[str] = (),
    next_action: str = "",
    no_material_gaps: bool = False,
    alignment_pending: bool = False,
    replace: bool = False,
    handoff: Path | None = None,
    force: bool = False,
    budget: int = 1400,
    include_context: bool = True,
) -> dict[str, Any]:
    root = root.resolve()
    if budget < 128:
        raise WorkEntryError("--budget must be >= 128")
    if no_material_gaps and alignment_pending:
        raise WorkEntryError("--no-material-gaps and --alignment-pending are mutually exclusive")

    clean_goal = str(goal or "").strip()
    scopes = _values(scope)
    constraints_clean = _values(constraints)
    done = _values(definition_of_done)
    next_clean = str(next_action or "").strip()

    if handoff is not None:
        if clean_goal or scopes or constraints_clean or done or next_clean or replace:
            raise WorkEntryError(
                "--handoff cannot be combined with new-task fields or --replace"
            )
        try:
            resumed = resume_handoff(
                root,
                handoff,
                target=root,
                force=force,
                budget=budget,
                include_context=include_context,
            )
        except HandoffError as exc:
            raise WorkEntryError(str(exc)) from exc
        state = load_task_state(root)
        if state is not None:
            state = _fast_align_if_requested(root, state, no_material_gaps)
            if no_material_gaps:
                brief = build_brief(
                    root,
                    mode="resume",
                    budget=budget,
                    include_context=include_context,
                )
            else:
                brief = resumed["brief"]
        else:
            brief = resumed["brief"]
        return {
            "format": "agent-devtools-work-entry",
            "formatVersion": 1,
            "action": "handoff-resumed",
            "task": state,
            "alignmentReady": bool(state and task_alignment_ready(state)),
            "brief": brief,
            "handoff": {
                "path": str(handoff.expanduser().resolve()),
                "kind": resumed.get("kind"),
                "originalWorkingStateFingerprint": resumed.get("originalWorkingStateFingerprint"),
                "currentWorkingStateFingerprint": resumed.get("currentWorkingStateFingerprint"),
            },
        }

    if force:
        raise WorkEntryError("--force is only valid with --handoff")

    state = load_task_state(root)
    if state is not None and state.get("status") == "active" and not replace:
        if clean_goal and clean_goal != str(state.get("goal") or ""):
            raise WorkEntryError(
                "an active task already exists with a different goal; "
                "resume it without --goal, complete it, or use --replace explicitly"
            )
        if scopes or constraints_clean or done or next_clean:
            raise WorkEntryError(
                "new-task fields cannot modify an existing active task through work enter; "
                "use task update or --replace explicitly"
            )
        state = _fast_align_if_requested(root, state, no_material_gaps)
        brief = build_brief(
            root,
            mode="resume",
            budget=budget,
            include_context=include_context,
        )
        return {
            "format": "agent-devtools-work-entry",
            "formatVersion": 1,
            "action": "resumed",
            "task": state,
            "alignmentReady": task_alignment_ready(state),
            "brief": brief,
            "handoff": None,
        }

    if not clean_goal:
        if state is not None and state.get("status") == "completed":
            raise WorkEntryError(
                "the previous task is completed; provide --goal to enter a new work item"
            )
        raise WorkEntryError(
            "no active task exists; provide --goal to start a new work item"
        )

    state = start_task(
        root,
        goal=clean_goal,
        scope=scopes,
        constraints=constraints_clean,
        definition_of_done=done,
        next_step=next_clean,
        replace=bool(state is not None or replace),
    )
    state = _fast_align_if_requested(
        root,
        state,
        not alignment_pending,
    )
    brief = build_brief(
        root,
        mode="work",
        budget=budget,
        include_context=include_context,
    )
    return {
        "format": "agent-devtools-work-entry",
        "formatVersion": 1,
        "action": "started",
        "task": state,
        "alignmentReady": task_alignment_ready(state),
        "brief": brief,
        "handoff": None,
    }
