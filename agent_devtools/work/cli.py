from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from .brief import _latest_run, build_brief, render_brief
from .completion import complete_work
from .entry import WorkEntryError, enter_work
from .checkpoint import CheckpointError, create_checkpoint, inspect_checkpoint, restore_checkpoint
from .state import TaskStateError, align_task, clear_task, complete_task, load_task_state, start_task, update_task, utc_now
from .knowledge import KnowledgeError, conflicts as knowledge_conflicts, effective_lifecycle_statuses, effective_statuses, explain as explain_knowledge, knowledge_status, load_records as load_knowledge_records, promote as promote_knowledge, remember as remember_knowledge, set_lifecycle as set_knowledge_lifecycle, supersede as supersede_knowledge, validate_knowledge, soft_contradictions
from .journal import SemanticJournalError, append_event, journal_status
from .semantic_closeout import semantic_checkpoint
from .verification import RESEARCH_CHECKS, VerificationError, latest_verification, record_verification, verification_status, record_research_bundle
from .sources import SourceError, add_source, list_sources
from agent_devtools.profiles import ProfileError, load_profile
from agent_devtools.work.context_projection import ContextProjectionError, prepare_context


def configure_task_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="task_command", required=True)
    start = sub.add_parser("start", help="start a tiny local task-continuity record")
    start.add_argument("--goal", required=True)
    start.add_argument("--scope", action="append", default=[])
    start.add_argument("--constraint", action="append", default=[])
    start.add_argument("--done", action="append", default=[], help="definition-of-done item")
    start.add_argument("--next-step", default="")
    start.add_argument("--replace", action="store_true")
    start.add_argument("--json", action="store_true", dest="json_output")

    update = sub.add_parser("update", help="record compact progress without a workflow/FSM")
    update.add_argument("--goal")
    update.add_argument("--add-scope", action="append", default=[])
    update.add_argument("--add-constraint", action="append", default=[])
    update.add_argument("--add-done", action="append", default=[])
    update.add_argument("--decision", action="append", default=[])
    update.add_argument("--finding", action="append", default=[])
    update.add_argument("--assumption", action="append", default=[])
    update.add_argument("--blocker", action="append", default=[])
    update.add_argument("--resolve-blocker", action="append", default=[])
    update.add_argument("--changed", action="append", default=[])
    update.add_argument("--verified", action="append", default=[])
    update.add_argument("--summary")
    update.add_argument("--next-step")
    update.add_argument("--json", action="store_true", dest="json_output")

    show = sub.add_parser("show", help="show current local task state")
    show.add_argument("--json", action="store_true", dest="json_output")
    clear = sub.add_parser("clear", help="remove local task state")
    clear.add_argument("--yes", action="store_true", help="confirm local task-state deletion")


def configure_brief_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--budget", type=int, default=1400, help="approximate context token budget")
    parser.add_argument("--before", type=Path, default=None, help="optional before source tree for semantic-diff-aware resume context")
    parser.add_argument("--no-context", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--out", type=Path, default=None, help="also save the rendered/JSON briefing")



def configure_verify_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="verify_command", required=True)
    record = sub.add_parser("record", help="record profile-neutral verification evidence")
    record.add_argument("--label", required=True)
    record.add_argument("--status", choices=("pass", "fail"), required=True)
    record.add_argument("--evidence", action="append", default=[])
    record.add_argument("--summary", default="")
    record.add_argument("--json", action="store_true", dest="json_output")
    status = sub.add_parser("status", help="show latest profile-neutral verification evidence")
    status.add_argument("--json", action="store_true", dest="json_output")
    research = sub.add_parser("research", help="review or record the structured research verification bundle")
    for name in ("arithmetic", "sourcing", "assumptions", "knowledge", "unresolved-questions"):
        research.add_argument(f"--{name}", choices=("pass", "warn", "fail"))
    research.add_argument(
        "--confirm-all-pass",
        action="store_true",
        help="after reviewing the guided checklist, explicitly attest that all five research verification dimensions pass",
    )
    research.add_argument("--evidence", action="append", default=[])
    research.add_argument("--summary", default="")
    research.add_argument("--json", action="store_true", dest="json_output")


_RESEARCH_REVIEW = {
    "arithmetic": "Calculations and quantitative comparisons were checked, or explicitly marked not applicable.",
    "sourcing": "Important factual claims are supported by adequate source provenance.",
    "assumptions": "Material assumptions are explicit and their effect on the conclusion was reviewed.",
    "knowledge": "Durable conclusions/requirements that should survive the session are represented in project knowledge.",
    "unresolved_questions": "Material unresolved questions are either closed or explicitly retained.",
}


def _research_review_payload() -> dict:
    return {
        "format": "agent-devtools-research-verification-review",
        "formatVersion": 1,
        "status": "review-required",
        "checks": [{"id": name, "prompt": _RESEARCH_REVIEW[name]} for name in RESEARCH_CHECKS],
        "guidance": {
            "rule": "Review all five dimensions before recording PASS; do not use the compact attestation when any item should be warn/fail.",
            "nextCommands": [
                "agent verify research --confirm-all-pass --summary \"<what was verified>\"",
                "agent verify research --arithmetic <pass|warn|fail> --sourcing <pass|warn|fail> --assumptions <pass|warn|fail> --knowledge <pass|warn|fail> --unresolved-questions <pass|warn|fail> --summary \"<what was verified>\"",
            ],
        },
    }


def main_verify(root: Path, args: argparse.Namespace) -> int:
    review_only = False
    try:
        if args.verify_command == "record":
            item = record_verification(root, label=args.label, status=args.status, evidence=args.evidence, summary=args.summary)
            payload = {"format": "agent-devtools-verification", "formatVersion": 1, "record": item}
        elif args.verify_command == "research":
            raw_checks = {
                "arithmetic": args.arithmetic,
                "sourcing": args.sourcing,
                "assumptions": args.assumptions,
                "knowledge": args.knowledge,
                "unresolved_questions": args.unresolved_questions,
            }
            provided = [value is not None for value in raw_checks.values()]
            confirm_all = bool(getattr(args, "confirm_all_pass", False))
            if confirm_all and any(provided):
                raise VerificationError(
                    "--confirm-all-pass cannot be combined with granular research statuses; "
                    "use the granular form when any dimension is warn/fail"
                )
            if not confirm_all and not any(provided):
                payload = _research_review_payload()
                review_only = True
            elif not confirm_all and not all(provided):
                raise VerificationError(
                    "partial research verification is not recordable; run `agent verify research` for the guided review "
                    "or provide all five pass/warn/fail statuses"
                )
            else:
                checks = (
                    {name: "pass" for name in RESEARCH_CHECKS}
                    if confirm_all
                    else {name: str(raw_checks[name]) for name in RESEARCH_CHECKS}
                )
                item = record_research_bundle(root, checks=checks, evidence=args.evidence, summary=args.summary)
                payload = {"format": "agent-devtools-verification", "formatVersion": 1, "record": item}
        else:
            payload = {"format": "agent-devtools-verification", "formatVersion": 1, **verification_status(root)}
    except VerificationError as exc:
        print(f"agent verify: {exc}", file=sys.stderr)
        return 2
    if getattr(args, "json_output", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif review_only:
        print("Research verification review:")
        for item in payload["checks"]:
            print(f"  {item['id']}: {item['prompt']}")
        print("Next, after reviewing all five dimensions:")
        print("  " + payload["guidance"]["nextCommands"][0])
        print("If any dimension is warn/fail, use the granular command shown by `agent verify research --json`.")
    else:
        item = payload.get("record") or payload.get("latest")
        if item:
            print(f"verification: {item['status'].upper()} · {item['label']} · {item['completedAtUtc']}")
        else:
            print("verification: none")
    if review_only:
        return 1
    if args.verify_command in {"record", "research"} and payload["record"]["status"] == "fail":
        return 1
    return 0

def configure_cognition_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="cognition_command", required=True)
    for name in ("observation", "decision", "finding", "assumption", "requirement", "open-question", "evidence", "blocker"):
        cmd = sub.add_parser(name, help=f"record one {name} in current task state")
        cmd.add_argument("text", nargs="?", help="semantic text (canonical positional form)")
        cmd.add_argument("--text", dest="text_option", help="natural alias for the positional semantic text")
        if name != "blocker":
            cmd.add_argument("--subject", help="optional durable-knowledge subject for reusable/durable classification")
        cmd.add_argument("--json", action="store_true", dest="json_output")
    resolve = sub.add_parser("resolve-blocker", help="resolve an exact recorded blocker")
    resolve.add_argument("text")
    resolve.add_argument("--json", action="store_true", dest="json_output")
    status = sub.add_parser("status", help="show compact cognition state")
    status.add_argument("--json", action="store_true", dest="json_output")
    checkpoint = sub.add_parser("checkpoint", help="classify current semantic journal for closeout; optionally promote required durable candidates explicitly")
    checkpoint.add_argument("--promote-required", action="store_true", help="promote every required durable candidate through knowledge remember, then re-run the checkpoint")
    checkpoint.add_argument("--json", action="store_true", dest="json_output")

    tool_failure = sub.add_parser("tool-failure", help="record repeated external-tool failure and get bounded recovery guidance")
    tool_failure.add_argument("--tool", required=True, help="tool/capability name, for example write or browser")
    tool_failure.add_argument("--operation", default="", help="short attempted operation label")
    tool_failure.add_argument("--error", required=True, help="stable error class/message from the failed attempt")
    tool_failure.add_argument("--importance", choices=("optional", "mandatory"), required=True)
    tool_failure.add_argument("--attempts", type=int, default=2, help="number of equivalent failed attempts observed")
    tool_failure.add_argument("--fallback", default="", help="available alternative path, if any")
    tool_failure.add_argument("--json", action="store_true", dest="json_output")


def _cognition_text(args: argparse.Namespace) -> str:
    positional = str(getattr(args, "text", "") or "").strip()
    optional = str(getattr(args, "text_option", "") or "").strip()
    if positional and optional:
        raise TaskStateError("provide cognition text either positionally or with --text, not both")
    value = positional or optional
    if not value:
        raise TaskStateError("cognition text is required (positional text or --text)")
    return value


def _tool_failure_recovery(root: Path, args: argparse.Namespace) -> tuple[dict, dict]:
    state = load_task_state(root)
    if state is None:
        raise TaskStateError("no task state exists; use begin or work start first")
    tool = str(args.tool or "").strip()
    operation = str(args.operation or "").strip()
    error = str(args.error or "").strip()
    importance = str(args.importance or "").strip()
    fallback = str(args.fallback or "").strip()
    attempts = int(args.attempts)
    if not tool or not error:
        raise TaskStateError("tool-failure requires non-empty --tool and --error")
    if attempts < 1:
        raise TaskStateError("tool-failure --attempts must be >= 1")

    fingerprint = hashlib.sha256("\0".join((tool, operation, error)).encode("utf-8")).hexdigest()[:16]
    retry_ceiling = 2
    ceiling_reached = attempts >= retry_ceiling
    retry_allowed = not ceiling_reached
    blocker = ""

    if retry_allowed:
        recommended = "retry-once"
    elif importance == "optional":
        recommended = "use-fallback" if fallback else "skip-optional-step"
    else:
        recommended = "use-fallback-and-resolve-blocker" if fallback else "stop-and-resolve-blocker"
        blocker = f"mandatory tool failure [{fingerprint}] {tool}" + (f"/{operation}" if operation else "") + f": {error}"
        state = update_task(root, add_blockers=[blocker])

    append_event(
        root,
        task_id=str(state.get("taskId") or ""),
        kind="observation",
        text=f"tool failure {tool}" + (f"/{operation}" if operation else "") + f" attempt {attempts}: {error}",
        metadata={
            "source": "cognition.tool-failure",
            "tool": tool,
            "operation": operation,
            "error": error,
            "fingerprint": fingerprint,
            "importance": importance,
            "attempts": attempts,
            "retryCeiling": retry_ceiling,
            "retryCeilingReached": ceiling_reached,
            "fallback": fallback,
            "blocking": bool(blocker),
        },
        created_at_utc=utc_now(),
    )

    try:
        profile_id = load_profile(root).profile_id
    except ProfileError:
        profile_id = ""

    next_commands: list[str] = []
    if blocker:
        next_commands.append(f'agent cognition resolve-blocker "{blocker}"')
    elif ceiling_reached and profile_id == "research":
        next_commands.extend(["agent verify research", "agent cognition checkpoint", "agent work complete"])
    elif ceiling_reached:
        next_commands.extend(["agent cognition checkpoint", "agent work complete"])

    return {
        "format": "agent-devtools-tool-failure-recovery",
        "formatVersion": 1,
        "tool": tool,
        "operation": operation,
        "error": error,
        "fingerprint": fingerprint,
        "importance": importance,
        "attempts": attempts,
        "retryCeiling": retry_ceiling,
        "retryAllowed": retry_allowed,
        "blocking": bool(blocker),
        "blocker": blocker or None,
        "fallback": fallback or None,
        "recommendedAction": recommended,
        "progressPreserved": True,
        "nextCommands": next_commands,
        "policy": "Do not repeat the same tool/action after two equivalent failures. Optional work must fallback or skip; mandatory work blocks completion until recovered.",
    }, state


def main_cognition(root: Path, args: argparse.Namespace) -> int:
    try:
        if args.cognition_command == "tool-failure":
            payload, state = _tool_failure_recovery(root, args)
            if args.json_output:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"Tool failure: {payload['tool']} · attempts={payload['attempts']}/{payload['retryCeiling']} · importance={payload['importance']} · action={payload['recommendedAction']}")
                if payload["retryAllowed"]:
                    print("Next: retry this exact tool/action at most once.")
                elif payload["importance"] == "optional":
                    print("Retry ceiling reached. Do not loop; use the fallback or skip this optional step.")
                    if payload["fallback"]:
                        print(f"Fallback: {payload['fallback']}")
                    print("Preserve completed findings/evidence and continue the canonical lifecycle.")
                else:
                    print("Retry ceiling reached. Mandatory step is now a blocker.")
                    if payload["fallback"]:
                        print(f"Fallback: {payload['fallback']}")
                    print(f"Blocker: {payload['blocker']}")
                for command in payload["nextCommands"]:
                    print(f"Next command: {command}")
            return 1 if payload["blocking"] else 0
        if args.cognition_command == "checkpoint":
            payload = semantic_checkpoint(root)
            promoted = []
            if bool(getattr(args, "promote_required", False)):
                before = payload
                for item in before["requiredPromotions"]:
                    promoted.append(remember_knowledge(root, event_id=str(item["eventId"])))
                payload = semantic_checkpoint(root)
                payload["promotion"] = {
                    "mode": "required-explicit",
                    "beforeRequired": len(before["requiredPromotions"]),
                    "promoted": [record["id"] for record in promoted],
                }
            payload["guidance"] = {
                "requiredAction": bool(payload["requiredPromotions"]),
                "advisoryOnly": bool(payload["advisoryCandidates"]) and not bool(payload["requiredPromotions"]),
                "promotionNeeded": bool(payload["requiredPromotions"] or payload["advisoryCandidates"]),
                "sessionOnly": len(payload.get("sessionOnlyEvents", [])),
                "routineRule": "Do not call agent knowledge promote manually; use --subject during cognition and let checkpoint classify durable candidates.",
                "nextCommands": (
                    ["agent cognition checkpoint --promote-required", "agent work complete"]
                    if payload["requiredPromotions"]
                    else ["agent work complete"]
                ),
            }
            if args.json_output:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(
                    f"Semantic checkpoint: events={payload['events']} · "
                    f"required={len(payload['requiredPromotions'])} · "
                    f"advisory={len(payload['advisoryCandidates'])} · "
                    f"session-only={len(payload.get('sessionOnlyEvents', []))}"
                )
                if promoted:
                    for record in promoted:
                        print(f"PROMOTED {record['kind']} · {record['subject']} · {record['id']}")
                for item in payload["requiredPromotions"]:
                    print(f"REQUIRED {item['kind']} · {item['subject']} · {item['text']}")
                for item in payload["advisoryCandidates"]:
                    print(f"REVIEW {item['kind']} · {item['subject']} · {item['text']}")
                if payload["requiredPromotions"]:
                    print("Next:")
                    print("  review REQUIRED candidates")
                    print("  agent cognition checkpoint --promote-required")
                    print("  agent work complete")
                elif payload["advisoryCandidates"]:
                    print("Advisory candidates do not block completion.")
                    for item in payload["advisoryCandidates"]:
                        print(f"  optional durable candidate: agent knowledge remember {item['eventId']}")
                    print("Next: agent work complete")
                else:
                    print("Durable knowledge: no promotion needed.")
                    if payload.get("sessionOnlyEvents"):
                        print(f"  {len(payload['sessionOnlyEvents'])} semantic event(s) remain session-only by design.")
                    print("Next: agent work complete")
                print("Routine rule: Do not call `agent knowledge promote` manually; record reusable cognition with --subject and let checkpoint classify it.")
            return 0 if payload["clean"] else 1
        subject = str(getattr(args, "subject", "") or "").strip()
        semantic_text = ""
        if args.cognition_command not in {"status", "resolve-blocker"}:
            semantic_text = _cognition_text(args)
        if args.cognition_command == "status":
            state = load_task_state(root)
            if state is None:
                raise TaskStateError("no task state exists; use work start or task start first")
        elif args.cognition_command == "observation":
            state = load_task_state(root)
            if state is None:
                raise TaskStateError("no task state exists; use work start or task start first")
            append_event(
                root,
                task_id=str(state.get("taskId") or ""),
                kind="observation",
                text=semantic_text,
                subject=subject,
                metadata={"source": "cognition.observation"},
                created_at_utc=utc_now(),
            )
        else:
            kwargs = {}
            subject_key = None
            if args.cognition_command == "decision":
                kwargs["add_decisions"], subject_key = [semantic_text], "decisions"
            elif args.cognition_command == "finding":
                kwargs["add_findings"], subject_key = [semantic_text], "findings"
            elif args.cognition_command == "assumption":
                kwargs["add_assumptions"], subject_key = [semantic_text], "assumptions"
            elif args.cognition_command == "requirement":
                kwargs["add_requirements"], subject_key = [semantic_text], "requirements"
            elif args.cognition_command == "open-question":
                kwargs["add_open_questions"], subject_key = [semantic_text], "openQuestions"
            elif args.cognition_command == "evidence":
                kwargs["add_evidence"], subject_key = [semantic_text], "evidence"
            elif args.cognition_command == "blocker":
                kwargs["add_blockers"] = [semantic_text]
            elif args.cognition_command == "resolve-blocker":
                kwargs["resolve_blockers"] = [args.text]
            if subject and subject_key:
                kwargs["semantic_subjects"] = {subject_key: subject}
            state = update_task(root, **kwargs)
    except (TaskStateError, SemanticJournalError, KnowledgeError) as exc:
        print(f"agent cognition: {exc}")
        return 2
    kind_map = {"open-question": "open_question"}
    knowledge_kind = kind_map.get(args.cognition_command, args.cognition_command)
    warnings = soft_contradictions(root, kind=knowledge_kind, text=semantic_text, subject=subject) if subject and args.cognition_command not in {"status", "resolve-blocker", "blocker", "observation"} else []
    payload = {
        "format": "agent-devtools-cognition", "formatVersion": 3,
        "taskId": state.get("taskId"), "status": state.get("status"),
        "decisions": state.get("decisions", []), "findings": state.get("findings", []),
        "assumptions": state.get("assumptions", []), "requirements": state.get("requirements", []),
        "openQuestions": state.get("openQuestions", []), "evidence": state.get("evidence", []), "blockers": state.get("blockers", []),
        "nextAction": state.get("nextStep", ""), "subject": subject or None, "knowledgeWarnings": warnings,
        "journal": journal_status(root, str(state.get("taskId") or "")),
    }
    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Cognition: task={payload['taskId']} status={payload['status']}")
        for key, title in (("decisions","decisions"),("findings","findings"),("assumptions","assumptions"),("requirements","requirements"),("openQuestions","open questions"),("evidence","evidence"),("blockers","blockers")):
            values = payload[key]
            if values: print(f"{title}: " + "; ".join(values))
        if payload["nextAction"]: print(f"next: {payload['nextAction']}")
        for warning in payload.get("knowledgeWarnings", []):
            print(f"KNOWLEDGE WARNING {warning['recordId']} · {warning['subject']} · {warning['statement']}")
    return 0


def configure_begin_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--goal", help="new work goal; omit to resume the active task")
    parser.add_argument("--scope", action="append", default=[])
    parser.add_argument("--constraint", action="append", default=[])
    parser.add_argument("--done", action="append", default=[])
    parser.add_argument("--next-action", default="")
    parser.add_argument("--alignment-pending", action="store_true")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--handoff", type=Path, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--budget", type=int, default=1400)
    parser.add_argument("--no-context", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")


def main_begin(root: Path, args: argparse.Namespace) -> int:
    try:
        entry = enter_work(
            root,
            goal=args.goal,
            scope=args.scope,
            constraints=args.constraint,
            definition_of_done=args.done,
            next_action=args.next_action,
            alignment_pending=bool(args.alignment_pending),
            replace=bool(args.replace),
            handoff=args.handoff,
            force=bool(args.force),
            budget=args.budget,
            include_context=False,
        )
        task = entry.get("task") if isinstance(entry.get("task"), dict) else {}
        projection = None
        if not args.no_context:
            projection = prepare_context(
                root,
                task=str(task.get("goal") or args.goal or "").strip() or None,
                stage="orient",
                scope=task.get("scope", []) or args.scope,
                budget=args.budget,
            )
        canonical_routine = ["begin", "work", "checkpoint", "complete"]
        resumed_active = entry.get("action") == "resumed" and not str(args.goal or "").strip()
        recovery = {
            "mode": "active-task-resume" if resumed_active else "normal-entry",
            "interruptionSafe": bool(resumed_active),
            "conversationMemoryAuthoritative": False,
            "projectStateAuthoritative": True,
            "canonicalRecoveryCommand": "agent begin",
        }
        payload = {
            "format": "agent-devtools-work-ritual-begin",
            "formatVersion": 2,
            "lifecycle": "agent-work-lifecycle",
            "canonicalRoutine": canonical_routine,
            "ritual": canonical_routine,
            "entry": entry,
            "knowledge": knowledge_status(root),
            "contextProjection": projection,
            "nextAction": task.get("nextStep") or "continue the current work item",
            "recovery": recovery,
        }
    except (TaskStateError, WorkEntryError, KnowledgeError, ContextProjectionError) as exc:
        print(f"agent begin: {exc}", file=sys.stderr)
        return 2
    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"BEGIN: {entry['action']} · "
            f"alignment={'ready' if entry['alignmentReady'] else 'pending'} · "
            f"knowledge={payload['knowledge']['records']}"
        )
        if projection is not None:
            if projection["selectedRecords"]:
                print(
                    f"  durable context: {projection['selectedRecords']} selected · "
                    f"stage={projection['stage']} · ~{projection['estimatedTokens']}/{projection['budget']} tokens"
                )
                for item in projection.get("knowledge", []):
                    print(f"  [{item['kind']}] {item['subject']} · {item['expandRef']}")
            else:
                print("  durable context: none yet")
        if payload["recovery"]["mode"] == "active-task-resume":
            print("  interruption recovery: active task resumed from project state")
            print("  project task state is authoritative; do not reconstruct progress from chat memory")
        print(f"  next: {payload['nextAction']}")
        print("  canonical routine: work -> checkpoint -> complete")
    return 0


def configure_work_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="work_command", required=True)
    enter = sub.add_parser("enter", help="start, resume, or restore work through one lifecycle-aware entrypoint")
    enter.add_argument("--goal")
    enter.add_argument("--scope", action="append", default=[])
    enter.add_argument("--constraint", action="append", default=[])
    enter.add_argument("--done", action="append", default=[])
    enter.add_argument("--next-action", default="")
    alignment_mode = enter.add_mutually_exclusive_group()
    alignment_mode.add_argument("--no-material-gaps", action="store_true", help="explicitly confirm the routine fast path; retained for compatibility")
    alignment_mode.add_argument("--alignment-pending", action="store_true", help="keep a new task pending for explicit material-gap assessment")
    enter.add_argument("--replace", action="store_true")
    enter.add_argument("--handoff", type=Path, default=None)
    enter.add_argument("--force", action="store_true", help="allow divergent restore only with --handoff")
    enter.add_argument("--budget", type=int, default=1400)
    enter.add_argument("--no-context", action="store_true")
    enter.add_argument("--json", action="store_true", dest="json_output")

    start = sub.add_parser("start", help="start work and emit an orientation briefing")
    start.add_argument("--goal", required=True)
    start.add_argument("--scope", action="append", default=[])
    start.add_argument("--constraint", action="append", default=[])
    start.add_argument("--done", action="append", default=[])
    start.add_argument("--next-action", default="")
    start.add_argument("--replace", action="store_true")
    start.add_argument("--budget", type=int, default=1400)
    start.add_argument("--json", action="store_true", dest="json_output")

    align = sub.add_parser("align", help="resolve material task gaps before substantial execution")
    action = align.add_mutually_exclusive_group(required=True)
    action.add_argument("--gap", action="append", default=[], help="material ambiguity that is expensive to get wrong; repeat as needed")
    action.add_argument("--user-approved", action="store_true", help="record explicit user approval after clarification")
    action.add_argument("--no-material-gaps", action="store_true", help="frictionless fast path when the task is already sufficiently specified; no user turn is required")
    align.add_argument("--proposal", default="", help="recommended concrete option(s) presented to the user")
    align.add_argument("--summary", default="", help="approval/resolution summary; optional for --no-material-gaps")
    align.add_argument("--json", action="store_true", dest="json_output")

    status = sub.add_parser("status", help="show cognition + context + check-plan briefing")
    status.add_argument("--budget", type=int, default=1400)
    status.add_argument("--before", type=Path, default=None)
    status.add_argument("--no-context", action="store_true")
    status.add_argument("--json", action="store_true", dest="json_output")

    complete = sub.add_parser("complete", help="reuse current PASS or run required checks, then finish")
    complete.add_argument("--summary")
    complete.add_argument("--no-cache", action="store_true", help="force selected checks to execute even when current PASS exists")
    complete.add_argument("--resume", action="store_true", help="resume compatible development verification chunks")
    complete.add_argument("--json", action="store_true", dest="json_output")

    finish = sub.add_parser("finish", help="lower-level finish after verification already exists")
    finish.add_argument("--summary")
    finish.add_argument("--json", action="store_true", dest="json_output")


def main_work(root: Path, args: argparse.Namespace) -> int:
    try:
        if args.work_command == "enter":
            payload = enter_work(
                root,
                goal=args.goal,
                scope=args.scope,
                constraints=args.constraint,
                definition_of_done=args.done,
                next_action=args.next_action,
                no_material_gaps=bool(args.no_material_gaps),
                alignment_pending=bool(args.alignment_pending),
                replace=bool(args.replace),
                handoff=args.handoff,
                force=bool(args.force),
                budget=args.budget,
                include_context=not args.no_context,
            )
        elif args.work_command == "start":
            start_task(root, goal=args.goal, scope=args.scope, constraints=args.constraint,
                       definition_of_done=args.done, next_step=args.next_action, replace=args.replace)
            payload = build_brief(root, mode="work", budget=args.budget, include_context=True)
        elif args.work_command == "align":
            state = align_task(
                root,
                material_gaps=args.gap,
                proposal=args.proposal,
                user_approved=args.user_approved,
                no_material_gaps=args.no_material_gaps,
                resolution=args.summary,
            )
            payload = {
                "format": "agent-devtools-work-alignment",
                "formatVersion": 1,
                "taskId": state.get("taskId"),
                "goal": state.get("goal"),
                "taskAlignment": state.get("taskAlignment"),
            }
        elif args.work_command == "status":
            payload = build_brief(root, mode="work", budget=args.budget,
                                  include_context=not args.no_context, before_root=args.before)
        elif args.work_command == "complete":
            payload = complete_work(
                root,
                summary=args.summary,
                run_verification=True,
                no_cache=bool(args.no_cache),
                resume=bool(args.resume),
            )
        elif args.work_command == "finish":
            payload = complete_work(root, summary=args.summary, run_verification=False)
            payload["format"] = "agent-devtools-work-finish"
        else:
            return 2
    except (TaskStateError, WorkEntryError, VerificationError) as exc:
        print(f"agent work: {exc}")
        return 2
    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.work_command in {"complete", "finish"}:
        print(f"PASS: work completed · {payload['task']['goal']}")
        action = payload.get("verificationAction", "recorded")
        print(f"  verification: {action}")
        if payload.get("verificationPerformed") and payload.get("verificationOutput"):
            print("  " + payload["verificationOutput"].replace("\n", "\n  "))
        print(f"  knowledge: {'PASS' if payload['knowledgeValidation']['ok'] else 'RECONCILE'}")
    elif args.work_command == "enter":
        print(
            f"ENTER: {payload['action']} · "
            f"alignment={'ready' if payload['alignmentReady'] else 'pending'}"
        )
        print(render_brief(payload["brief"]))
    elif args.work_command == "align":
        alignment = payload["taskAlignment"]
        print(f"Task alignment: {alignment['status']}")
        if alignment.get("materialGaps"):
            print("Material gaps: " + "; ".join(alignment["materialGaps"]))
        if alignment.get("proposal"):
            print("Proposal: " + alignment["proposal"])
        if alignment.get("resolution"):
            print("Resolution: " + alignment["resolution"])
    else:
        print(render_brief(payload))
    return 0



def configure_knowledge_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="knowledge_command", required=True)
    promote = sub.add_parser("promote", help="expert primitive: promote exact recorded cognition; routine workflow should use cognition + checkpoint classification")
    promote.add_argument("kind", choices=("decision", "finding", "assumption", "requirement", "open_question", "evidence"))
    promote.add_argument("text", help="exact cognition text already recorded in current task")
    promote.add_argument("--subject", required=True, help="stable human-readable knowledge subject")
    promote.add_argument("--anchor", action="append", default=[], help="semantic identity related to this knowledge")
    promote.add_argument("--supersedes", action="append", default=[], help="older durable record id superseded by this record")
    promote.add_argument("--scope", action="append", default=[], help="durable hierarchical scope; defaults to current task scope")
    promote.add_argument("--confidence", choices=("low", "medium", "high"))
    promote.add_argument("--evidence-ref", action="append", default=[], help="durable verification/evidence reference")
    promote.add_argument("--author", help="optional human/team author label; defaults to AGENT_DEVTOOLS_AUTHOR")
    promote.add_argument("--agent-environment", help="optional agent environment label; defaults to AGENT_DEVTOOLS_AGENT_ENVIRONMENT")
    promote.add_argument("--workstation", help="optional workstation label; defaults to AGENT_DEVTOOLS_WORKSTATION")
    promote.add_argument("--source", action="append", default=[], help="tracked research source id supporting this knowledge")
    promote.add_argument("--json", action="store_true", dest="json_output")

    remember = sub.add_parser("remember", help="promote one subject-bearing semantic journal event by event id")
    remember.add_argument("event_id")
    remember.add_argument("--scope", action="append", default=[], help="durable scope; defaults to current task scope")
    remember.add_argument("--confidence", choices=("low", "medium", "high"))
    remember.add_argument("--evidence-ref", action="append", default=[])
    remember.add_argument("--json", action="store_true", dest="json_output")

    lifecycle = sub.add_parser("lifecycle", help="mark one durable record active, historical or rejected")
    lifecycle.add_argument("record_id")
    lifecycle.add_argument("status", choices=("active", "historical", "rejected"))
    lifecycle.add_argument("--json", action="store_true", dest="json_output")

    supersede_cmd = sub.add_parser("supersede", help="link an active successor to an older record without rewriting the older file")
    supersede_cmd.add_argument("older_id")
    supersede_cmd.add_argument("--by", required=True, dest="newer_id")
    supersede_cmd.add_argument("--json", action="store_true", dest="json_output")

    why = sub.add_parser("why", help="show durable provenance, lifecycle and relations for one knowledge record")
    why.add_argument("record_id")
    why.add_argument("--json", action="store_true", dest="json_output")

    listing = sub.add_parser("list", help="list durable tracked project knowledge")
    listing.add_argument("--kind", choices=("decision", "finding", "assumption", "requirement", "open_question", "evidence", "source"))
    listing.add_argument("--subject")
    listing.add_argument("--json", action="store_true", dest="json_output")
    status = sub.add_parser("status", help="show durable knowledge counts and reconciliation health")
    status.add_argument("--json", action="store_true", dest="json_output")
    validate = sub.add_parser("validate", help="validate durable knowledge and report conflicts/invalid lifecycle relations")
    validate.add_argument("--json", action="store_true", dest="json_output")
    conflicts_cmd = sub.add_parser("conflicts", help="show conflicting active decisions for the same subject")
    conflicts_cmd.add_argument("--json", action="store_true", dest="json_output")


def main_knowledge(root: Path, args: argparse.Namespace) -> int:
    try:
        if args.knowledge_command == "promote":
            warnings = soft_contradictions(root, kind=args.kind, text=args.text, subject=args.subject, supersedes=args.supersedes)
            record = promote_knowledge(
                root,
                kind=args.kind,
                text=args.text,
                subject=args.subject,
                anchors=args.anchor,
                supersedes=args.supersedes,
                author=args.author,
                agent_environment=args.agent_environment,
                workstation=args.workstation,
                source_refs=args.source,
                scope=args.scope,
                confidence=args.confidence,
                evidence_refs=args.evidence_ref,
            )
            payload = {"status": "promoted", "record": record, "knowledgeWarnings": warnings}
        elif args.knowledge_command == "remember":
            record = remember_knowledge(
                root,
                event_id=args.event_id,
                scope=args.scope,
                confidence=args.confidence,
                evidence_refs=args.evidence_ref,
            )
            payload = {"status": "remembered", "record": record}
        elif args.knowledge_command == "lifecycle":
            record = set_knowledge_lifecycle(root, record_id=args.record_id, lifecycle_status=args.status)
            payload = {"status": "updated", "record": record}
        elif args.knowledge_command == "supersede":
            record = supersede_knowledge(root, older_id=args.older_id, newer_id=args.newer_id)
            payload = {"status": "superseded", "record": record, "olderId": args.older_id}
        elif args.knowledge_command == "why":
            payload = explain_knowledge(root, args.record_id)
        elif args.knowledge_command == "list":
            records = load_knowledge_records(root)
            statuses = effective_statuses(records)
            lifecycle = effective_lifecycle_statuses(records)
            records = [
                {
                    **item,
                    "effectiveStatus": statuses[item["id"]],
                    "effectiveLifecycleStatus": lifecycle[item["id"]],
                }
                for item in records
            ]
            if args.kind:
                records = [item for item in records if item["kind"] == args.kind]
            if args.subject:
                records = [item for item in records if item["subject"] == args.subject]
            payload = {"records": records, "count": len(records)}
        elif args.knowledge_command == "status":
            payload = knowledge_status(root)
        elif args.knowledge_command == "validate":
            payload = validate_knowledge(root)
        else:
            items = knowledge_conflicts(root)
            payload = {"conflicts": items, "count": len(items)}
    except (KnowledgeError, TaskStateError) as exc:
        print(f"agent knowledge: {exc}", file=sys.stderr)
        return 2
    if getattr(args, "json_output", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if args.knowledge_command in {"promote", "remember", "lifecycle", "supersede"}:
            record = payload["record"]
            print(
                f"KNOWLEDGE {payload['status'].upper()} {record['kind']} {record['id']} · "
                f"{record['subject']}"
            )
            for warning in payload.get("knowledgeWarnings", []):
                print(f"KNOWLEDGE WARNING {warning['recordId']} · {warning['subject']} · {warning['statement']}")
        elif args.knowledge_command == "why":
            record = payload["record"]
            print(
                f"{record['id']} {record['kind']} {payload['effectiveLifecycleStatus']} · "
                f"{record['subject']} · {record['statement']}"
            )
            session = payload.get("sourceSession") or {}
            if session:
                print("  source session: " + ", ".join(f"{key}={value}" for key, value in session.items()))
            if payload.get("scope"):
                print("  scope: " + ", ".join(payload["scope"]))
            if payload.get("confidence"):
                print(f"  confidence: {payload['confidence']}")
            if payload.get("supersedes"):
                print("  supersedes: " + ", ".join(payload["supersedes"]))
            if payload.get("supersededBy"):
                print("  superseded by: " + ", ".join(payload["supersededBy"]))
        elif args.knowledge_command == "status":
            print(
                f"knowledge: {payload['records']} records · "
                f"{len(payload['conflicts'])} conflicts · "
                f"{len(payload['danglingSupersedes'])} dangling · "
                f"{'PASS' if payload['ok'] else 'RECONCILE'}"
            )
            print(f"  canonical: {payload['canonicalStore']} · durable SQLite={'yes' if payload['sqliteDurableStore'] else 'no'}")
            if payload["byKind"]:
                print("  kinds: " + ", ".join(f"{key}={value}" for key, value in payload["byKind"].items()))
            if payload["byLifecycleStatus"]:
                print("  lifecycle: " + ", ".join(f"{key}={value}" for key, value in payload["byLifecycleStatus"].items()))
        elif args.knowledge_command == "validate":
            print(
                f"knowledge: {payload['records']} records · {len(payload['conflicts'])} conflicts · "
                f"{len(payload.get('invalidSupersedes', []))} invalid supersedes · "
                f"{len(payload.get('supersessionCycles', []))} cycles · "
                f"{'PASS' if payload['ok'] else 'RECONCILE'}"
            )
        elif args.knowledge_command == "conflicts":
            print(f"knowledge conflicts: {payload['count']}")
            for item in payload["conflicts"]:
                print(f"  {item['subject']}: " + ", ".join(item["recordIds"]))
        else:
            for item in payload["records"]:
                print(
                    f"{item['id']} {item['kind']} {item.get('effectiveLifecycleStatus', 'active')} · "
                    f"{item['subject']} · {item['statement']}"
                )
    if args.knowledge_command == "validate" and not payload.get("ok", True):
        return 1
    return 0

def configure_checkpoint_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="checkpoint_command", required=True)
    create = sub.add_parser("create", help="create a portable recovery ZIP for current unfinished work")
    create.add_argument("--out", type=Path, default=None)
    create.add_argument("--include", action="append", default=[], help="explicit project-relative file; useful without Git")
    create.add_argument("--max-bytes", type=int, default=100 * 1024 * 1024)
    create.add_argument("--json", action="store_true", dest="json_output")
    inspect = sub.add_parser("inspect", help="verify and inspect a checkpoint without changing files")
    inspect.add_argument("checkpoint", type=Path)
    inspect.add_argument("--json", action="store_true", dest="json_output")
    restore = sub.add_parser("restore", help="safely restore checkpointed working files/task state")
    restore.add_argument("checkpoint", type=Path)
    restore.add_argument("--force", action="store_true", help="allow base mismatch/divergent overwrite")
    restore.add_argument("--json", action="store_true", dest="json_output")


def _print_task(state: dict) -> None:
    print(f"Task: {state.get('goal')}")
    if state.get("summary"):
        print(f"progress: {state.get('summary')}")
    if state.get("nextStep"):
        print(f"next: {state.get('nextStep')}")
    for key, title in (
        ("scope", "scope"),
        ("constraints", "constraints"),
        ("definitionOfDone", "done when"),
        ("decisions", "decisions"),
        ("findings", "findings"),
        ("assumptions", "assumptions"),
        ("requirements", "requirements"),
        ("openQuestions", "open questions"),
        ("evidence", "evidence"),
        ("blockers", "blockers"),
        ("changedFiles", "changed"),
        ("verification", "verified"),
    ):
        values = state.get(key) or []
        if values:
            print(f"{title}: " + "; ".join(values))
    print(f"updated: {state.get('updatedAtUtc')}")


def main_task(root: Path, args: argparse.Namespace) -> int:
    try:
        if args.task_command == "start":
            state = start_task(root, goal=args.goal, scope=args.scope, constraints=args.constraint,
                               definition_of_done=args.done, next_step=args.next_step, replace=args.replace)
        elif args.task_command == "update":
            state = update_task(root, goal=args.goal, add_scope=args.add_scope, add_constraints=args.add_constraint,
                                add_done=args.add_done, add_decisions=args.decision, add_findings=args.finding, add_assumptions=args.assumption, add_blockers=args.blocker,
                                changed_files=args.changed, verification=args.verified, resolve_blockers=args.resolve_blocker, summary=args.summary,
                                next_step=args.next_step)
        elif args.task_command == "show":
            state = load_task_state(root)
            if state is None:
                print("agent task show: no task state")
                return 1
        elif args.task_command == "clear":
            if not args.yes:
                print("agent task clear: pass --yes to confirm local task-state deletion")
                return 2
            removed = clear_task(root)
            print("task state cleared" if removed else "no task state")
            return 0
        else:
            return 2
    except TaskStateError as exc:
        print(f"agent task: {exc}")
        return 2
    if getattr(args, "json_output", False):
        print(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        _print_task(state)
    return 0


def main_brief(root: Path, args: argparse.Namespace, *, mode: str) -> int:
    if args.budget < 128:
        print(f"agent {mode}: --budget must be >= 128")
        return 2
    payload = build_brief(root, mode=mode, budget=args.budget, include_context=not args.no_context, before_root=args.before)
    output = json.dumps(payload, ensure_ascii=False, indent=2) if args.json_output else render_brief(payload)
    print(output)
    if args.out:
        out = args.out.expanduser()
        if not out.is_absolute():
            out = (root / out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(output + "\n", encoding="utf-8")
    return 0


def main_checkpoint(root: Path, args: argparse.Namespace) -> int:
    try:
        if args.checkpoint_command == "create":
            payload = create_checkpoint(root, out=args.out, include=args.include, max_bytes=args.max_bytes)
        elif args.checkpoint_command == "inspect":
            payload = inspect_checkpoint(args.checkpoint)
        elif args.checkpoint_command == "restore":
            payload = restore_checkpoint(root, args.checkpoint, force=args.force)
        else:
            return 2
    except (CheckpointError, TaskStateError) as exc:
        print(f"agent checkpoint: {exc}")
        return 2
    if getattr(args, "json_output", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if args.checkpoint_command == "create":
            print(
                f"PASS: checkpoint {payload['files']} file(s), {payload['deleted']} deletion(s), "
                f"sha256={payload['sha256']}\n  {payload['path']}"
            )
        elif args.checkpoint_command == "inspect":
            print(
                f"PASS: checkpoint {payload['files']} file(s), {payload['deleted']} deletion(s), "
                f"task={'yes' if payload['taskIncluded'] else 'no'} · {payload['path']}"
            )
        else:
            print(
                f"PASS: restored {payload['restoredFiles']} file(s), deleted {payload['deletedFiles']}, "
                f"task={'yes' if payload['taskRestored'] else 'no'}"
            )
    return 0

def configure_source_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="source_command", required=True)
    add = sub.add_parser("add", help="add one research/document source with optional claims")
    add.add_argument("--url", required=True)
    add.add_argument("--title", required=True)
    add.add_argument("--claim", action="append", default=[])
    add.add_argument("--accessed-at", default=None)
    add.add_argument("--json", action="store_true", dest="json_output")
    listing = sub.add_parser("list", help="list tracked research/document sources")
    listing.add_argument("--json", action="store_true", dest="json_output")


def main_source(root: Path, args: argparse.Namespace) -> int:
    try:
        if args.source_command == "add":
            item = add_source(root, url=args.url, title=args.title, claims=args.claim, accessed_at=args.accessed_at)
            payload = {"format": "agent-devtools-research-source", "formatVersion": 1, "source": item}
        else:
            items = list_sources(root)
            payload = {"format": "agent-devtools-research-sources", "formatVersion": 1, "sources": items, "count": len(items)}
    except SourceError as exc:
        print(f"agent source: {exc}", file=sys.stderr)
        return 2
    if getattr(args, "json_output", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.source_command == "add":
        print(f"SOURCE {payload['source']['id']} · {payload['source']['title']}")
    else:
        for item in payload["sources"]:
            print(f"{item.get('id')} · {item.get('title')} · {item.get('url')}")
    return 0
