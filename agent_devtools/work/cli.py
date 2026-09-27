from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .brief import _latest_run, build_brief, render_brief
from .checkpoint import CheckpointError, create_checkpoint, inspect_checkpoint, restore_checkpoint
from .state import TaskStateError, clear_task, complete_task, load_task_state, start_task, update_task
from .knowledge import KnowledgeError, conflicts as knowledge_conflicts, effective_statuses, load_records as load_knowledge_records, promote as promote_knowledge, validate_knowledge, soft_contradictions
from .verification import VerificationError, latest_verification, record_verification, verification_status, record_research_bundle
from .sources import SourceError, add_source, list_sources
from agent_devtools.profiles import ProfileError, load_profile


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
    research = sub.add_parser("research", help="record structured research verification bundle")
    for name in ("arithmetic", "sourcing", "assumptions", "knowledge", "unresolved-questions"):
        research.add_argument(f"--{name}", choices=("pass", "warn", "fail"), required=True)
    research.add_argument("--evidence", action="append", default=[])
    research.add_argument("--summary", default="")
    research.add_argument("--json", action="store_true", dest="json_output")


def main_verify(root: Path, args: argparse.Namespace) -> int:
    try:
        if args.verify_command == "record":
            item = record_verification(root, label=args.label, status=args.status, evidence=args.evidence, summary=args.summary)
            payload = {"format": "agent-devtools-verification", "formatVersion": 1, "record": item}
        elif args.verify_command == "research":
            checks = {"arithmetic": args.arithmetic, "sourcing": args.sourcing, "assumptions": args.assumptions, "knowledge": args.knowledge, "unresolved_questions": args.unresolved_questions}
            item = record_research_bundle(root, checks=checks, evidence=args.evidence, summary=args.summary)
            payload = {"format": "agent-devtools-verification", "formatVersion": 1, "record": item}
        else:
            payload = {"format": "agent-devtools-verification", "formatVersion": 1, **verification_status(root)}
    except VerificationError as exc:
        print(f"agent verify: {exc}", file=sys.stderr)
        return 2
    if getattr(args, "json_output", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        item = payload.get("record") or payload.get("latest")
        if item:
            print(f"verification: {item['status'].upper()} · {item['label']} · {item['completedAtUtc']}")
        else:
            print("verification: none")
    if args.verify_command in {"record", "research"} and payload["record"]["status"] == "fail":
        return 1
    return 0

def configure_cognition_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="cognition_command", required=True)
    for name in ("decision", "finding", "assumption", "requirement", "open-question", "evidence", "blocker"):
        cmd = sub.add_parser(name, help=f"record one {name} in current task state")
        cmd.add_argument("text")
        if name != "blocker":
            cmd.add_argument("--subject", help="optional durable-knowledge subject for soft contradiction checking")
        cmd.add_argument("--json", action="store_true", dest="json_output")
    resolve = sub.add_parser("resolve-blocker", help="resolve an exact recorded blocker")
    resolve.add_argument("text")
    resolve.add_argument("--json", action="store_true", dest="json_output")
    status = sub.add_parser("status", help="show compact cognition state")
    status.add_argument("--json", action="store_true", dest="json_output")


def main_cognition(root: Path, args: argparse.Namespace) -> int:
    try:
        if args.cognition_command == "status":
            state = load_task_state(root)
            if state is None:
                raise TaskStateError("no task state exists; use work start or task start first")
        else:
            kwargs = {}
            if args.cognition_command == "decision": kwargs["add_decisions"] = [args.text]
            elif args.cognition_command == "finding": kwargs["add_findings"] = [args.text]
            elif args.cognition_command == "assumption": kwargs["add_assumptions"] = [args.text]
            elif args.cognition_command == "requirement": kwargs["add_requirements"] = [args.text]
            elif args.cognition_command == "open-question": kwargs["add_open_questions"] = [args.text]
            elif args.cognition_command == "evidence": kwargs["add_evidence"] = [args.text]
            elif args.cognition_command == "blocker": kwargs["add_blockers"] = [args.text]
            elif args.cognition_command == "resolve-blocker": kwargs["resolve_blockers"] = [args.text]
            state = update_task(root, **kwargs)
    except TaskStateError as exc:
        print(f"agent cognition: {exc}")
        return 2
    subject = str(getattr(args, "subject", "") or "").strip()
    kind_map = {"open-question": "open_question"}
    knowledge_kind = kind_map.get(args.cognition_command, args.cognition_command)
    warnings = soft_contradictions(root, kind=knowledge_kind, text=getattr(args, "text", ""), subject=subject) if subject and args.cognition_command not in {"status", "resolve-blocker", "blocker"} else []
    payload = {
        "format": "agent-devtools-cognition", "formatVersion": 2,
        "taskId": state.get("taskId"), "status": state.get("status"),
        "decisions": state.get("decisions", []), "findings": state.get("findings", []),
        "assumptions": state.get("assumptions", []), "requirements": state.get("requirements", []),
        "openQuestions": state.get("openQuestions", []), "evidence": state.get("evidence", []), "blockers": state.get("blockers", []),
        "nextAction": state.get("nextStep", ""), "subject": subject or None, "knowledgeWarnings": warnings,
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


def configure_work_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="work_command", required=True)
    start = sub.add_parser("start", help="start work and emit an orientation briefing")
    start.add_argument("--goal", required=True)
    start.add_argument("--scope", action="append", default=[])
    start.add_argument("--constraint", action="append", default=[])
    start.add_argument("--done", action="append", default=[])
    start.add_argument("--next-action", default="")
    start.add_argument("--replace", action="store_true")
    start.add_argument("--budget", type=int, default=1400)
    start.add_argument("--json", action="store_true", dest="json_output")

    status = sub.add_parser("status", help="show cognition + context + check-plan briefing")
    status.add_argument("--budget", type=int, default=1400)
    status.add_argument("--before", type=Path, default=None)
    status.add_argument("--no-context", action="store_true")
    status.add_argument("--json", action="store_true", dest="json_output")

    finish = sub.add_parser("finish", help="finish current work after a successful verification run")
    finish.add_argument("--summary")
    finish.add_argument("--json", action="store_true", dest="json_output")


def main_work(root: Path, args: argparse.Namespace) -> int:
    try:
        if args.work_command == "start":
            start_task(root, goal=args.goal, scope=args.scope, constraints=args.constraint,
                       definition_of_done=args.done, next_step=args.next_action, replace=args.replace)
            payload = build_brief(root, mode="work", budget=args.budget, include_context=True)
        elif args.work_command == "status":
            payload = build_brief(root, mode="work", budget=args.budget,
                                  include_context=not args.no_context, before_root=args.before)
        elif args.work_command == "finish":
            state = load_task_state(root)
            if state is None:
                raise TaskStateError("no task state exists; use work start first")
            try:
                profile = load_profile(root)
            except ProfileError as exc:
                raise TaskStateError(str(exc)) from exc
            latest = _latest_run(root) if profile.verification_mode == "check" else latest_verification(root)
            if not latest or str(latest.get("status") or "").lower() not in {"pass", "passed", "success", "ok"}:
                noun = "Agent DevTools check run" if profile.verification_mode == "check" else "verification record"
                raise TaskStateError(f"cannot finish work without a successful {noun}")
            completed = str(latest.get("completedAtUtc") or "")
            started = str(state.get("startedAtUtc") or "")
            if not completed or (started and completed < started):
                raise TaskStateError("cannot finish work with verification older than the current task")
            state = complete_task(root, summary=args.summary)
            payload = {"format": "agent-devtools-work-finish", "formatVersion": 1, "profile": profile.profile_id, "task": state, "latestVerification": latest}
        else:
            return 2
    except TaskStateError as exc:
        print(f"agent work: {exc}")
        return 2
    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.work_command == "finish":
        print(f"PASS: work completed · {payload['task']['goal']}")
    else:
        print(render_brief(payload))
    return 0



def configure_knowledge_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="knowledge_command", required=True)
    promote = sub.add_parser("promote", help="promote one current-session cognition record into tracked project knowledge")
    promote.add_argument("kind", choices=("decision", "finding", "assumption", "requirement", "open_question", "evidence"))
    promote.add_argument("text", help="exact cognition text already recorded in current task")
    promote.add_argument("--subject", required=True, help="stable human-readable knowledge subject")
    promote.add_argument("--anchor", action="append", default=[], help="semantic identity related to this knowledge")
    promote.add_argument("--supersedes", action="append", default=[], help="older durable record id superseded by this record")
    promote.add_argument("--author", help="optional human/team author label; defaults to AGENT_DEVTOOLS_AUTHOR")
    promote.add_argument("--agent-environment", help="optional agent environment label; defaults to AGENT_DEVTOOLS_AGENT_ENVIRONMENT")
    promote.add_argument("--workstation", help="optional workstation label; defaults to AGENT_DEVTOOLS_WORKSTATION")
    promote.add_argument("--source", action="append", default=[], help="tracked research source id supporting this knowledge")
    promote.add_argument("--json", action="store_true", dest="json_output")
    listing = sub.add_parser("list", help="list durable tracked project knowledge")
    listing.add_argument("--kind", choices=("decision", "finding", "assumption", "requirement", "open_question", "evidence", "source"))
    listing.add_argument("--subject")
    listing.add_argument("--json", action="store_true", dest="json_output")
    validate = sub.add_parser("validate", help="validate durable knowledge and report unresolved decision conflicts")
    validate.add_argument("--json", action="store_true", dest="json_output")
    conflicts_cmd = sub.add_parser("conflicts", help="show conflicting active decisions for the same subject")
    conflicts_cmd.add_argument("--json", action="store_true", dest="json_output")


def main_knowledge(root: Path, args: argparse.Namespace) -> int:
    try:
        if args.knowledge_command == "promote":
            warnings = soft_contradictions(root, kind=args.kind, text=args.text, subject=args.subject, supersedes=args.supersedes)
            record = promote_knowledge(root, kind=args.kind, text=args.text, subject=args.subject, anchors=args.anchor, supersedes=args.supersedes, author=args.author, agent_environment=args.agent_environment, workstation=args.workstation, source_refs=args.source)
            payload = {"status": "promoted", "record": record, "knowledgeWarnings": warnings}
        elif args.knowledge_command == "list":
            records = load_knowledge_records(root)
            statuses = effective_statuses(records)
            records = [{**item, "effectiveStatus": statuses[item["id"]]} for item in records]
            if args.kind:
                records = [item for item in records if item["kind"] == args.kind]
            if args.subject:
                records = [item for item in records if item["subject"] == args.subject]
            payload = {"records": records, "count": len(records)}
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
        if args.knowledge_command == "promote":
            record = payload["record"]
            print(f"PROMOTED {record['kind']} {record['id']} · {record['subject']}")
            for warning in payload.get("knowledgeWarnings", []):
                print(f"KNOWLEDGE WARNING {warning['recordId']} · {warning['subject']} · {warning['statement']}")
        elif args.knowledge_command == "validate":
            print(f"knowledge: {payload['records']} records · {len(payload['conflicts'])} conflicts · {'PASS' if payload['ok'] else 'RECONCILE'}")
        elif args.knowledge_command == "conflicts":
            print(f"knowledge conflicts: {payload['count']}")
            for item in payload['conflicts']:
                print(f"  {item['subject']}: " + ", ".join(item['recordIds']))
        else:
            for item in payload['records']:
                print(f"{item['id']} {item['kind']} {item.get('effectiveStatus', item['status'])} · {item['subject']} · {item['statement']}")
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
