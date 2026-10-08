from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .affected import build_affected_briefing, resolve_changed
from .config import ContextConfigError, load_context_config
from .index import ContextIndexError, ensure_index, index_stats, validate_context
from .search import SearchResult, inspect_identity, query_context
from .semantic_diff import compare_semantic_states
from agent_devtools.work.context_projection import ContextProjectionError, current_context, expand_context, prepare_context, why_selected


def configure_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="context_command", required=True)
    prepare = sub.add_parser("prepare", help="assemble a budgeted stage-aware projection from current task + durable knowledge")
    prepare.add_argument("--task", default=None, help="optional task/query override; defaults to current work goal")
    prepare.add_argument("--stage", choices=("planning", "implementation", "verification", "documentation", "checkpointing", "handoff", "operator_review"), default=None)
    prepare.add_argument("--scope", action="append", default=[], help="additional hierarchical scope; repeat as needed")
    prepare.add_argument("--path", action="append", default=[], help="additional relevant project path; repeat as needed")
    prepare.add_argument("--budget", type=int, default=1400, help="approximate token budget for projected durable knowledge")
    prepare.add_argument("--dedup-threshold", type=float, default=0.60)
    prepare.add_argument("--include-historical", action="store_true", help="include non-active lifecycle/semantic knowledge for explicit historical review")
    prepare.add_argument("--json", action="store_true", dest="json_output")
    current = sub.add_parser("current", help="show the structured current-task and operational projection without knowledge retrieval")
    current.add_argument("--stage", choices=("planning", "implementation", "verification", "documentation", "checkpointing", "handoff", "operator_review"), default=None)
    current.add_argument("--json", action="store_true", dest="json_output")
    why = sub.add_parser("why", help="explain why one knowledge record was selected by the last context prepare")
    why.add_argument("knowledge_id")
    why.add_argument("--json", action="store_true", dest="json_output")
    expand = sub.add_parser("expand", help="expand a knowledge:<id> reference from a compact context cue")
    expand.add_argument("reference")
    expand.add_argument("--json", action="store_true", dest="json_output")
    ensure = sub.add_parser("ensure", help="create/update the disposable local context index")
    ensure.add_argument("--rebuild", action="store_true")
    ensure.add_argument("--config", type=Path, default=None)
    ensure.add_argument("--json", action="store_true", dest="json_output")
    rebuild = sub.add_parser("rebuild", help="force an atomic full context-index rebuild")
    rebuild.add_argument("--config", type=Path, default=None)
    rebuild.add_argument("--json", action="store_true", dest="json_output")
    validate = sub.add_parser("validate", help="validate semantic anchors/selectors without persisting knowledge")
    validate.add_argument("--config", type=Path, default=None)
    validate.add_argument("--json", action="store_true", dest="json_output")
    query = sub.add_parser("query", help="assemble a small relevance-filtered project context packet")
    query.add_argument("query")
    query.add_argument("--limit", type=int, default=8)
    query.add_argument("--budget", type=int, default=None, help="hard approximate token budget")
    query.add_argument("--max-chars", type=int, default=None, help="legacy compatibility; converted to an approximate token budget")
    query.add_argument("--config", type=Path, default=None)
    query.add_argument("--json", action="store_true", dest="json_output")
    inspect = sub.add_parser("inspect", help="show one stable semantic/symbol/structural identity")
    inspect.add_argument("identity")
    inspect.add_argument("--config", type=Path, default=None)
    inspect.add_argument("--json", action="store_true", dest="json_output")
    stats = sub.add_parser("stats", help="show local context-index state")
    stats.add_argument("--config", type=Path, default=None)
    stats.add_argument("--json", action="store_true", dest="json_output")
    affected = sub.add_parser("affected", help="assemble impact-first context for the current/explicit change set")
    affected.add_argument("--changed", action="append", default=[])
    affected.add_argument("--base", default=None)
    affected.add_argument("--before", type=Path, default=None, help="optional before source tree; changed semantic identities are derived and prioritized")
    affected.add_argument("--profile", default="affected")
    affected.add_argument("--limit", type=int, default=10)
    affected.add_argument("--budget", type=int, default=None)
    affected.add_argument("--max-chars", type=int, default=None)
    affected.add_argument("--config", type=Path, default=None)
    affected.add_argument("--json", action="store_true", dest="json_output")
    diff = sub.add_parser("diff", help="compare stable semantic identities between two source trees")
    diff.add_argument("--before", type=Path, required=True, help="source tree used as the before state")
    diff.add_argument("--after", type=Path, default=None, help="source tree used as the after state; defaults to current project")
    diff.add_argument("--include-unchanged", action="store_true")
    diff.add_argument("--json", action="store_true", dest="json_output")


def _row(item: SearchResult) -> dict:
    return {
        "id": item.chunk_id,
        "path": item.path,
        "kind": item.kind,
        "label": item.label,
        "anchor": item.anchor,
        "selector": item.selector,
        "startLine": item.start_line,
        "endLine": item.end_line,
        "sourceKind": item.source_kind,
        "weight": item.weight,
        "priority": item.priority,
        "score": round(item.score, 6),
        "why": list(item.why),
        "content": item.content,
    }


def _budget(args: argparse.Namespace, default: int) -> int:
    if getattr(args, "budget", None) is not None:
        return int(args.budget)
    if getattr(args, "max_chars", None) is not None:
        return max(128, math.ceil(int(args.max_chars) / 4))
    return default


def _print_results(results: tuple[SearchResult, ...] | list[SearchResult]) -> None:
    for index, item in enumerate(results, start=1):
        identity = item.anchor or item.chunk_id
        selector = f" · {item.selector}" if item.selector else ""
        print(f"\n{index}. {item.path}:{item.start_line}-{item.end_line} · {item.kind} · {identity}{selector} · score={item.score:.2f}")
        print(f"   why: {', '.join(item.why)}")
        for line in item.content.rstrip().splitlines():
            print(f"   {line}")


def main(root: Path, args: argparse.Namespace) -> int:
    try:
        if args.context_command == "diff":
            before_root = args.before.expanduser().resolve()
            after_root = (args.after.expanduser() if args.after else root).resolve()
            if not before_root.is_dir():
                raise ContextConfigError(f"semantic diff --before is not a directory: {before_root}")
            if not after_root.is_dir():
                raise ContextConfigError(f"semantic diff --after is not a directory: {after_root}")
            before_config = load_context_config(before_root, None)
            after_config = load_context_config(after_root, None)
            report = compare_semantic_states(before_config, after_config, include_unchanged=bool(args.include_unchanged))
            if args.json_output:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                counts = report["counts"]
                print(f"semantic diff · added={counts['added']} removed={counts['removed']} modified={counts['modified']} unchanged={counts['unchanged']}")
                for item in report["changes"]:
                    before = item["before"]
                    after = item["after"]
                    if before is not None and after is not None:
                        location = (
                            f"{before['path']}:{before['startLine']}-{before['endLine']} -> "
                            f"{after['path']}:{after['startLine']}-{after['endLine']}"
                        )
                    else:
                        state = after or before
                        location = f"{state['path']}:{state['startLine']}-{state['endLine']}"
                    print(f"{item['status']}: {item['identity']} · {location}")
            return 0
        if args.context_command == "prepare":
            payload = prepare_context(
                root,
                task=args.task,
                stage=args.stage,
                scope=args.scope,
                paths=args.path,
                budget=args.budget,
                dedup_threshold=args.dedup_threshold,
                include_historical=bool(args.include_historical),
            )
            if args.json_output:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                task = payload.get("task") or {}
                operational = payload.get("operational") or {}
                print(
                    f"context projection · stage={payload['stage']} · "
                    f"selected={payload['selectedRecords']}/{payload['candidateRecords']} · "
                    f"budget={payload['estimatedTokens']}/{payload['budget']}"
                )
                if task.get("currentObjective"):
                    print(f"task: {task['currentObjective']}")
                if operational.get("requiredBeforeNextStage"):
                    print("required: " + "; ".join(operational["requiredBeforeNextStage"]))
                print(f"next safe action: {operational.get('nextSafeAction')}")
                for item in payload.get("knowledge", []):
                    text = item.get("statement") or item.get("summary") or ""
                    print(
                        f"  {item['id']} [{item['mode']}] {item['kind']} score={item['score']} · "
                        f"{item['subject']} · {text}"
                    )
                    print("    why: " + "; ".join(item.get("reason", [])))
                    print(f"    expand: {item['expandRef']}")
            return 0
        if args.context_command == "current":
            payload = current_context(root, stage=args.stage)
            if args.json_output:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                task = payload.get("task") or {}
                operational = payload.get("operational") or {}
                print(f"current task · stage={operational.get('stage')}")
                print(f"objective: {task.get('currentObjective') or 'none'}")
                if task.get("activeScope"):
                    print("scope: " + ", ".join(task["activeScope"]))
                if operational.get("blockers"):
                    print("blockers: " + "; ".join(operational["blockers"]))
                if operational.get("requiredBeforeNextStage"):
                    print("required: " + "; ".join(operational["requiredBeforeNextStage"]))
                print(f"next safe action: {operational.get('nextSafeAction')}")
            return 0
        if args.context_command == "why":
            payload = why_selected(root, args.knowledge_id)
            if args.json_output:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"{payload['id']} · score={payload['score']} · stage={payload['stage']} · mode={payload['mode']}")
                print("why: " + "; ".join(payload.get("reason", [])))
                print(f"expand: {payload['expandRef']}")
            return 0
        if args.context_command == "expand":
            payload = expand_context(root, args.reference)
            if args.json_output:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                record = payload["record"]
                print(
                    f"{record['id']} {record['kind']} {payload['effectiveLifecycleStatus']} · "
                    f"{record['subject']} · {record['statement']}"
                )
                if payload.get("scope"):
                    print("scope: " + ", ".join(payload["scope"]))
                if payload.get("evidenceRefs"):
                    print("evidence: " + ", ".join(payload["evidenceRefs"]))
            return 0
        config = load_context_config(root, getattr(args, "config", None))
        if args.context_command in {"ensure", "rebuild"}:
            report = ensure_index(config, rebuild=(args.context_command == "rebuild" or bool(getattr(args, "rebuild", False))))
            if args.json_output:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(
                    f"PASS: context {report['mode']} · {report['files']} files · {report['chunks']} chunks · "
                    f"changed={report['changed']} removed={report['removed']} · FTS5={'yes' if report['fts5'] else 'no'} · "
                    f"{report['elapsedSeconds']:.3f}s"
                )
            return 0
        if args.context_command == "validate":
            report = validate_context(config)
            if args.json_output:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(
                    f"PASS: context semantic metadata · {report['files']} files · {report['stableIdentities']} stable identities · "
                    f"anchors={report['explicitAnchors']} selectors={report['structuralSelectors']} sidecar={report['sidecarMappings']}"
                )
            return 0
        if args.context_command == "stats":
            report = index_stats(config)
            if args.json_output:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            elif not report.get("exists"):
                print(f"context index: missing · {report['database']}")
            else:
                print(
                    f"context index: {report['files']} files · {report['chunks']} chunks · {report['relations']} relations · "
                    f"{report['bytes']} bytes · FTS5={'yes' if report['fts5'] else 'no'}"
                )
            return 0
        if args.context_command == "affected":
            changed = resolve_changed(root, args.changed, args.base)
            semantic_identities: tuple[str, ...] = ()
            semantic_diff = None
            if args.before is not None:
                before_root = args.before.expanduser().resolve()
                if not before_root.is_dir():
                    raise ContextConfigError(f"affected --before is not a directory: {before_root}")
                before_config = load_context_config(before_root, None)
                semantic_diff = compare_semantic_states(before_config, config, include_unchanged=False)
                semantic_identities = tuple(
                    str(item["identity"]) for item in semantic_diff["changes"]
                    if item.get("status") in {"added", "removed", "modified"}
                )
            briefing = build_affected_briefing(
                root,
                config,
                changed_files=changed,
                profile=args.profile,
                limit=args.limit,
                budget=_budget(args, config.default_budget),
                semantic_identities=semantic_identities,
            )
            payload = {
                "changedFiles": list(briefing.changed_files),
                "semanticIdentities": list(briefing.semantic_identities),
                "semanticDiffCounts": semantic_diff["counts"] if semantic_diff is not None else None,
                "impactAvailable": briefing.impact_available,
                "initialImpact": list(briefing.initial_impact),
                "effectiveImpact": list(briefing.effective_impact),
                "selectedSuites": list(briefing.selected_suites),
                "selectedApplicationGroups": list(briefing.selected_application_groups),
                "fallbackFull": briefing.fallback_full,
                "fallbackReasons": list(briefing.fallback_reasons),
                "query": briefing.query,
                "scope": briefing.scope_payload(),
                "candidateFiles": briefing.candidate_files,
                "candidateChunks": briefing.candidate_chunks,
                "estimatedTokens": briefing.estimated_tokens,
                "budget": briefing.budget,
                "omitted": briefing.omitted,
                "results": [_row(item) for item in briefing.results],
            }
            if args.json_output:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                impact = ", ".join(briefing.effective_impact) or "unavailable"
                suites = ", ".join(briefing.selected_suites) or "none"
                print(f"affected context · changed={len(changed)} · semantic={len(briefing.semantic_identities)} · impact={impact} · suites={suites}")
                if briefing.fallback_full:
                    print("fallback full: " + "; ".join(briefing.fallback_reasons))
                if briefing.required_paths or briefing.preferred_paths:
                    print(
                        "scope: "
                        f"required={len(briefing.required_paths)} "
                        f"preferred={len(briefing.preferred_paths)} "
                        f"requiredPathBudgetExceeded={briefing.omitted.get('requiredPathBudgetExceeded', 0)}"
                    )
                print(
                    f"query: {briefing.query} · candidates={briefing.candidate_files} files/{briefing.candidate_chunks} chunks · "
                    f"budget={briefing.estimated_tokens}/{briefing.budget} estimated tokens"
                )
                if briefing.omitted:
                    print("omitted: " + ", ".join(f"{key}={value}" for key, value in sorted(briefing.omitted.items())))
                _print_results(briefing.results)
            return 0
        ensure_index(config)
        if args.context_command == "query":
            report = query_context(config, args.query, limit=args.limit, budget=_budget(args, config.default_budget))
            payload = {
                "query": args.query,
                "candidateFiles": report.candidate_files,
                "candidateChunks": report.candidate_chunks,
                "estimatedTokens": report.estimated_tokens,
                "budget": report.budget,
                "omitted": report.omitted,
                "results": [_row(item) for item in report.results],
            }
            if args.json_output:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(
                    f"context query: {args.query!r} · {len(report.results)} result(s) · "
                    f"candidates={report.candidate_files} files/{report.candidate_chunks} chunks · "
                    f"budget={report.estimated_tokens}/{report.budget} estimated tokens"
                )
                if report.omitted:
                    print("omitted: " + ", ".join(f"{key}={value}" for key, value in sorted(report.omitted.items())))
                _print_results(report.results)
            return 0
        if args.context_command == "inspect":
            item = inspect_identity(config, args.identity)
            if item is None:
                print(f"stable context identity not found: {args.identity}")
                return 1
            if args.json_output:
                print(json.dumps(_row(item), ensure_ascii=False, indent=2))
            else:
                print(f"{item.chunk_id} · {item.path}:{item.start_line}-{item.end_line}")
                if item.selector:
                    print(f"selector: {item.selector}")
                print(item.content, end="" if item.content.endswith("\n") else "\n")
            return 0
    except (ContextConfigError, ContextIndexError, ContextProjectionError) as exc:
        print(f"agent context: {exc}")
        return 2
    raise AssertionError(args.context_command)
