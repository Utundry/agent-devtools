from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import __version__
from .profiles import load_profile

CLI_CONTRACT_VERSION = 35
WORKFLOW_CONTRACT_VERSION = 23


def workflow_contract(root: Path) -> dict[str, Any]:
    profile = load_profile(root)
    return {
        "format": "agent-devtools-workflow",
        "formatVersion": WORKFLOW_CONTRACT_VERSION,
        "cliContractVersion": CLI_CONTRACT_VERSION,
        "toolVersion": __version__,
        "profile": {
            "id": profile.profile_id,
            "title": profile.title,
            "development": profile.development,
            "verificationMode": profile.verification_mode,
            "changeDiscovery": profile.change_discovery,
        },
        "principles": [
            "The Agent Work Lifecycle canonical routine is begin -> work -> checkpoint -> complete. begin is the normal entry facade over work enter plus durable context projection; lower-level commands remain authoritative primitives.",
            "Phases describe responsibilities, not seven separate command invocations. Routine work uses begin, project-native execution, checkpoint when semantic closeout needs review, and work complete.",
            "Use work complete for routine completion: reuse a successful check only after validating its current inputs, outputs, task identity and coverage; otherwise run the affected policy. No visible changes and no reusable evidence require a baseline check. work finish is the strict no-execution primitive; work complete --no-cache explicitly requests physical execution.",
            "Read relevant durable knowledge when needed; begin is the canonical first-pass retrieval for durable project memory. Trust its selected + possibly-related projection before doing any generic manual rescan of .agent-knowledge; use exact filesystem search only when that projection is insufficient for a specific reason or when exact repository text outside durable memory is needed. Promote only new reusable knowledge and reuse exact existing statements instead of creating duplicates.",
            "AGENTS.md defines the mandatory project workflow; the CLI is authoritative for current syntax and capabilities. Routine discovery is pull-based: do not run workflow show, capabilities --json, or help as a preamble when begin and the known normal surface are sufficient; inspect them only when syntax, feature availability, profile state, or recovery behavior is actually uncertain.",
            "Agent DevTools orchestrates project-native tools; it does not replace Git, build, test, release, or domain tooling.",
            "Canonical project change discovery includes untracked files governed by the source contract; plain git diff is not a complete handoff inventory.",
            "Session state and caches are disposable; durable project knowledge is tracked in .agent-knowledge/.",
            "Use preserve for profile-aware portable recovery state; lower-level checkpoint/workspace-snapshot commands remain available when their distinction matters.",
            "Use handoff to transfer unfinished work across chats or agents as one manifest-verified bundle containing both recoverable state and a resume briefing.",
            "Use begin as the canonical routine entrypoint. work enter remains the lifecycle primitive for debugging/integration and must not be guessed as the normal agent-facing route.",
            "Autonomy starts after alignment: ask only about material gaps that are expensive to get wrong; propose a concrete best option and obtain explicit user approval before substantial execution when such gaps exist. If the task is already sufficiently specified, record the no-gap fast path and proceed without a user turn.",
            "Alignment must be explicit in state, not necessarily visible as friction to the user. A newly created high-level work enter uses the routine no-gap fast path by default; use --alignment-pending when material-gap assessment is genuinely needed. Never ask a question merely to satisfy the gate.",
            "Meaningful cognition is append-only in the local semantic journal. Semantic text accepts positional or --text forms. Add --subject at capture time when reuse is intended; checkpoint is the routine authority that classifies required, advisory and session-only semantics before closeout.",
            "Durable project knowledge is canonical in tracked .agent-knowledge JSON files. SQLite remains disposable session/index state and must be rebuildable without loss of durable knowledge.",
            "Active context is a deterministic budgeted projection, not a second knowledge store: rank by task/stage/scope/path/terms, suppress near-duplicates, explain every primary selection, expose only a bounded separate possibly-related cue channel for weaker cross-task relevance, and exclude superseded/historical knowledge unless explicitly requested.",
            "Interactive shell is UX only: every shell action normalizes to the ordinary CLI dispatcher, creates no shell-only project state, and remains reproducible as a conventional command.",
            "External-tool failure is bounded: after two equivalent failures of the same tool/action, stop retrying. Optional work must fallback or skip while preserving completed progress; mandatory work becomes an ordinary blocker until recovered.",
            "Transport/chat/SSE interruption is recovered from project state, not conversation memory: on the next usable turn run begin without --goal, resume the active task, and continue from its returned next safe action.",
        ],
        "routineRoute": {
            "name": "Agent Work Lifecycle",
            "canonicalRoutine": ["begin", "work", "checkpoint", "complete"],
            "entry": "begin",
            "entryPrimitive": "work enter",
            "entryRule": "Routine agents use begin; work enter is a lower-level primitive.",
            "beginSufficientForKnownRoutine": True,
            "diagnosticsPullOnly": True,
            "diagnosticCommands": ["workflow show", "capabilities --json", "--help"],
            "manualAlignmentAfterReady": False,
            "execution": "Use project-native tools; record only meaningful new findings or decisions.",
            "completion": "work complete",
            "alreadyVerifiedCompletion": "work finish",
            "automaticVerificationReuse": True,
            "explicitExecution": "work complete --no-cache",
            "deepVerification": "Explicit certification, replay or release; not part of routine completion.",
            "contextIndex": "Optional; created only by explicit context operations, reused by briefings when present.",
            "knowledgePromotion": "Event-driven; checkpoint is read-only by default and prints executable next actions. checkpoint --promote-required is the explicit convenience path for required decisions/requirements only. Zero new records is valid when no durable candidate exists.",
            "knowledgeLifecycle": "Tracked .agent-knowledge JSON is canonical durable state; supersession is relation-derived and append-friendly.",
            "contextProjection": "Budgeted stage-aware projection from active durable knowledge with compact primary cues plus a separately bounded possibly-related cross-task cue channel; begin is the canonical first-pass durable retrieval and generic .agent-knowledge rescans are fallback-only; runtime usage state is disposable.",
            "durableRetrieval": {
                "firstPass": "begin",
                "trustProjectionFirst": True,
                "genericKnowledgeRescanRoutine": False,
                "fallbackRule": "Use exact filesystem search only when projected durable context is insufficient for a specific reason, an exact file/line is required, or repository content outside durable knowledge must be searched.",
            },
            "interactiveShell": "Optional UX over the same top-level CLI dispatcher; no shell-only state or write path.",
            "semanticJournal": "Append-only disposable SQLite session journal; cognition checkpoint classifies durable candidates before closeout.",
            "semanticCapture": "Use cognition with positional text, --text, --stdin or --from-file; add --subject only for reusable semantics, then let checkpoint classify required/advisory/session-only outcomes.",
            "finalReport": "work report",
            "directKnowledgePromotion": "expert primitive only; routine agents do not call knowledge promote before checkpoint classification.",
            "researchVerification": "Research routine uses verify research guided review, then explicit compact or granular attestation.",
            "toolFailureRecovery": "After two equivalent failures of the same tool/action, stop retrying: optional work falls back or skips without blocking; mandatory work becomes an ordinary blocker. Preserve completed cognition and continue from the nearest safe lifecycle step.",
            "interruptionResume": "After transport/chat/SSE interruption, run begin without --goal. Resume the existing active task from project state and its next action; do not reconstruct progress from conversation memory.",
            "surface": {
                "mode": "canonical-surface",
                "normalCommands": ["begin", "cognition", "verify", "cognition checkpoint", "work complete"],
                "advancedPrimitives": ["task update", "work enter", "work finish", "knowledge promote", "checkpoint create", "resume"],
                "rule": "Routine agents stay on the normal surface; advanced primitives remain available for expert, compatibility and recovery use."
            },
        },
        "phases": [
            {
                "id": "orient",
                "required": True,
                "purpose": "Recover project/task context and existing durable knowledge before substantial work.",
                "commands": ["begin", "work enter", "handoff resume", "resume", "work status", "knowledge status", "context prepare", "context current", "shell"],
            },
            {
                "id": "start",
                "required": True,
                "purpose": "Start or continue an explicit work session with a goal and next action.",
                "commands": ["begin", "work enter", "work start"],
            },
            {
                "id": "align",
                "required": True,
                "purpose": "Resolve material task gaps before substantial execution. When none exist, use the frictionless no-gap fast path and continue immediately without asking the user; when material gaps do exist, propose concrete options and obtain explicit approval.",
                "commands": ["work enter", "work align"],
            },
            {
                "id": "work",
                "required": True,
                "purpose": "Record meaningful findings, decisions, assumptions, and blockers while changing project artifacts.",
                "commands": ["cognition", "cognition checkpoint", "context"],
            },
            {
                "id": "verify",
                "required": True,
                "purpose": "Collect fresh profile-appropriate verification evidence. Research uses a guided review tray before explicit attestation; development continues to use project-native checks.",
                "commands": ["check", "verify", "changes status"],
            },
            {
                "id": "knowledge",
                "required": False,
                "purpose": "Promote lasting knowledge into Git-tracked files, manage lifecycle/provenance, and reconcile contradictions explicitly.",
                "commands": ["knowledge promote", "knowledge remember", "knowledge why", "knowledge supersede", "knowledge validate"],
            },
            {
                "id": "finish",
                "required": True,
                "purpose": "Complete in one action: validate gates, reuse fresh PASS or run the minimum sufficient checks, and finish only with current evidence.",
                "commands": ["work complete", "work finish"],
            },
        ],
    }



_CAPABILITY_SUMMARY_HISTORY: dict[str, dict[str, Any]] = {
    "0.16.4": {
        "cliContractVersion": 32,
        "workflowContractVersion": 22,
        "routineCommands": ["agent begin", "agent cognition", "agent verify", "agent cognition checkpoint", "agent work complete"],
        "diagnostics": ["agent workflow show", "agent capabilities --json", "agent --help"],
    },
    "0.16.5": {
        "cliContractVersion": 32,
        "workflowContractVersion": 22,
        "routineCommands": ["agent begin", "agent cognition", "agent verify", "agent cognition checkpoint", "agent work complete"],
        "diagnostics": ["agent workflow show", "agent capabilities --json", "agent --help"],
    },
    "0.16.6": {
        "cliContractVersion": 33,
        "workflowContractVersion": 22,
        "routineCommands": ["agent begin", "agent cognition", "agent verify", "agent cognition checkpoint", "agent work complete"],
        "diagnostics": ["agent workflow show", "agent capabilities --json", "agent --help"],
    },
    "0.16.7": {
        "cliContractVersion": 33,
        "workflowContractVersion": 22,
        "routineCommands": ["agent begin", "agent cognition", "agent verify", "agent cognition checkpoint", "agent work complete"],
        "diagnostics": ["agent workflow show", "agent capabilities --json", "agent --help"],
    },
    "0.16.8": {
        "cliContractVersion": 34,
        "workflowContractVersion": 22,
        "routineCommands": ["agent begin", "agent cognition", "agent verify", "agent cognition checkpoint", "agent work complete"],
        "diagnostics": ["agent workflow show", "agent capabilities --json", "agent --help"],
    },
    "0.16.9": {
        "cliContractVersion": 35,
        "workflowContractVersion": 23,
        "routineCommands": ["agent begin", "agent cognition", "agent verify", "agent cognition checkpoint", "agent work complete"],
        "diagnostics": ["agent workflow show", "agent capabilities --json", "agent --help"],
    },
    "0.16.10": {
        "cliContractVersion": 35,
        "workflowContractVersion": 23,
        "routineCommands": ["agent begin", "agent cognition", "agent verify", "agent cognition checkpoint", "agent work complete"],
        "diagnostics": ["agent workflow show", "agent capabilities --json", "agent --help"],
    },
}

_CAPABILITY_RELEASE_CHANGES: dict[str, list[str]] = {
    "0.16.5": [
        "declarative-update.contract-transitions",
        "declarative-update.stale-exact-assertion-guard",
    ],
    "0.16.6": [
        "capabilities.summary",
        "capabilities.diff",
        "source-update.onboarding-refresh",
    ],
    "0.16.7": [
        "declarative-update.fresh-process-per-edge",
        "declarative-update.periodic-fresh-auto-process",
    ],
    "0.16.8": [
        "cognition.batch",
        "cognition.one-line-success",
        "overhead.passive-cli-metrics",
    ],
    "0.16.9": [
        "workflow.begin-once-discipline",
        "workflow.batch-cognition-preference",
        "verification.research-warning-assessment",
        "verification.completion-gate-separation",
        "workflow.actionable-alignment-misuse-hint",
    ],
    "0.16.10": [
        "declarative-update.indirect-contract-assertion-guard",
    ],
    "0.16.11": [
        "context.passive-efficiency-telemetry",
        "context.repeat-projection-detection",
        "context.conservative-reference-signal",
    ],
}


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", str(value or "").strip())
    if not match:
        raise ValueError(f"unsupported semantic version for capability diff: {value!r}")
    return tuple(int(part) for part in match.groups())


def _release_changes_between(previous_version: str, current_version: str) -> list[str]:
    previous_key = _version_tuple(previous_version)
    current_key = _version_tuple(current_version)
    if previous_key > current_key:
        raise ValueError(
            f"capability diff baseline {previous_version!r} is newer than current {current_version!r}"
        )
    collected: list[str] = []
    for version in sorted(_CAPABILITY_RELEASE_CHANGES, key=_version_tuple):
        key = _version_tuple(version)
        if previous_key < key <= current_key:
            collected.extend(_CAPABILITY_RELEASE_CHANGES[version])
    return list(dict.fromkeys(collected))


def capability_summary(root: Path) -> dict[str, Any]:
    payload = capabilities(root)
    normal = payload["normalSurface"]
    return {
        "format": "agent-devtools-capability-summary",
        "formatVersion": 1,
        "toolVersion": payload["toolVersion"],
        "profile": payload["profile"]["profile_id"],
        "cliContractVersion": payload["cliContractVersion"],
        "workflowContractVersion": payload["workflowContractVersion"],
        "routineCommands": list(normal["commands"]),
        "diagnostics": list(normal.get("diagnostics", [])),
        "diagnosticsAreRoutine": bool(normal.get("diagnosticsAreRoutine", False)),
        "releaseChanges": list(_CAPABILITY_RELEASE_CHANGES.get(payload["toolVersion"], [])),
        "migrationWarnings": [],
    }


def capability_diff(root: Path, previous_version: str) -> dict[str, Any]:
    previous_version = str(previous_version or "").strip()
    previous = _CAPABILITY_SUMMARY_HISTORY.get(previous_version)
    if previous is None:
        supported = ", ".join(sorted(_CAPABILITY_SUMMARY_HISTORY)) or "none"
        raise ValueError(
            f"capability diff baseline {previous_version!r} is not bundled; supported: {supported}"
        )
    current = capability_summary(root)
    old_routine = set(previous["routineCommands"])
    new_routine = set(current["routineCommands"])
    old_diag = set(previous["diagnostics"])
    new_diag = set(current["diagnostics"])
    return {
        "format": "agent-devtools-capability-diff",
        "formatVersion": 1,
        "fromVersion": previous_version,
        "toVersion": current["toolVersion"],
        "contracts": {
            "cli": {
                "from": previous["cliContractVersion"],
                "to": current["cliContractVersion"],
            },
            "workflow": {
                "from": previous["workflowContractVersion"],
                "to": current["workflowContractVersion"],
            },
        },
        "routineAdded": sorted(new_routine - old_routine),
        "routineRemoved": sorted(old_routine - new_routine),
        "diagnosticsAdded": sorted(new_diag - old_diag),
        "diagnosticsRemoved": sorted(old_diag - new_diag),
        "newCapabilities": _release_changes_between(previous_version, current["toolVersion"]),
        "migrationWarnings": list(current["migrationWarnings"]),
    }


def capabilities(root: Path) -> dict[str, Any]:
    profile = load_profile(root)
    return {
        "format": "agent-devtools-capabilities",
        "formatVersion": 1,
        "cliContractVersion": CLI_CONTRACT_VERSION,
        "workflowContractVersion": WORKFLOW_CONTRACT_VERSION,
        "toolVersion": __version__,
        "profile": asdict(profile),
        "commands": {
            "workflow": {"show": True, "validate": True, "pullOnly": True, "routineRequired": False},
            "capabilities": {"json": True, "summary": True, "diff": True, "pullOnly": True, "routineRequired": False},
            "selfUpdate": {"available": True, "latestDiscovery": True, "explicitVersion": True, "checkOnly": True, "freshDownload": True, "identityVerified": True},
            "begin": {"available": True, "canonicalRoutineEntry": True, "usesWorkEnter": True, "durableContextProjection": True, "knowledgeHealth": True, "interruptionResume": True, "resumeWithoutGoal": True, "ordinaryFollowupReentryRequired": False, "interruptionRecoveryOnly": True, "profileHintCompatibility": True, "conversationMemoryAuthoritative": False, "projectStateAuthoritative": True, "newStateModel": False},
            "work": {"enter": True, "start": True, "align": True, "status": True, "complete": True, "finish": True, "report": True, "reportReadOnly": True, "oneActionEntry": True, "handoffEntry": True, "routineEntryFastPath": True, "alignmentPendingOptOut": True, "userApprovalRequiresRecordedGap": True, "oneActionCompletion": True, "automaticVerificationReuse": True, "baselineWhenUnverified": True, "taskGapGate": True, "noGapFastPath": True, "semanticCloseout": True},
            "cognition": {"observation": True, "decision": True, "finding": True, "assumption": True, "requirement": not profile.development, "openQuestion": not profile.development, "evidence": not profile.development, "blocker": True, "resolveBlocker": True, "batch": True, "batchDistinctEvents": True, "batchPerEventSource": True, "batchPreferredForMultiple": True, "toolFailure": True, "toolFailureRetryCeiling": 2, "toolFailureOptionalNonBlocking": True, "toolFailureMandatoryBlocks": True, "status": True, "checkpoint": True, "checkpointPromoteRequired": True, "textOptionAlias": True, "stdin": True, "fromFile": True, "utf8BulkCapture": True, "naturalGuessHints": True, "checkpointSessionOnlyClassification": True, "appendOnlyJournal": True, "possibleStaleCognition": True, "compactHumanOutput": True, "oneLineSuccess": True},
            "overheadMetrics": {"passive": True, "storage": ".agent-work/cli-overhead.jsonl + .agent-work/context-usage.sqlite3", "cliCalls": True, "durationMs": True, "stdoutBytes": True, "exitCode": True, "contextTokens": True, "retrievalReferenceRate": True, "repeatProjectionDetection": True, "noRoutineCommand": True},
            "knowledge": {"promote": True, "remember": True, "why": True, "lifecycle": True, "supersede": True, "validate": True, "status": True, "researchKinds": not profile.development, "softContradictionWarnings": True, "promotionRequired": False, "routineDirectPromote": False, "checkpointAuthority": True, "canonicalStore": ".agent-knowledge", "durableSqlite": False},
            "source": {"add": not profile.development, "list": not profile.development, "taskSessionProvenance": not profile.development},
            "context": {"available": True, "affected": profile.development, "prepare": True, "current": True, "why": True, "expand": True, "stageAware": True, "hierarchicalScope": True, "budgeted": True, "deduplicate": True, "selectionReasons": True, "crossTaskLexicalIsolation": True, "possiblyRelatedCues": True, "possiblyRelatedSeparateBudget": True, "canonicalFirstPassRetrieval": True, "manualDurableSearchFallbackOnly": True, "avoidRedundantDurableRescan": True, "usageSqliteDisposable": True, "efficiencyTelemetry": True, "repeatProjectionDetection": True, "conservativeReferenceSignal": True, "referenceSignalIsProofOfUse": False},
            "shell": {"available": True, "interactive": True, "batchCommand": True, "sameDispatcher": True, "shellOnlyState": False},
            "changes": {"status": profile.development, "patch": profile.development, "canonicalUntracked": profile.development, "workspaceLocalMarks": profile.development},
            "verification": {
                "check": profile.verification_mode == "check",
                "guidedResearchReview": profile.profile_id == "research",
                "researchConfirmAllPass": profile.profile_id == "research",
                "researchContextWarnings": profile.profile_id == "research",
                "researchSourceAwareness": profile.profile_id == "research",
                "researchMaterialQuestionAwareness": profile.profile_id == "research",
                "researchStrongWarningGate": profile.profile_id == "research",
                "researchAssessmentStatus": profile.profile_id == "research",
                "researchWarningChecksExplicit": profile.profile_id == "research",
                "completionGateSeparated": profile.profile_id == "research",
                "granularAttestationEscapeHatch": profile.profile_id == "research",
                "record": profile.verification_mode == "record",
                "researchBundle": profile.profile_id == "research",
                "researchCanonicalCommand": "verify research" if profile.profile_id == "research" else None,
                "resumableChunks": profile.verification_mode == "check",
                "idleWatchdog": profile.verification_mode == "check",
                "hardWatchdog": profile.verification_mode == "check",
                "machineCompletionBinding": profile.verification_mode == "check",
                "inheritedEnvironmentIdentity": profile.verification_mode == "check",
                "explicitCacheEnv": profile.verification_mode == "check",
                "reuseProvenance": profile.verification_mode == "check",
            },
            "resume": True,
            "handoff": {"create": True, "inspect": True, "resume": True, "profileAware": True, "manifestVerified": True, "freshResumeBrief": True},
            "preserve": {"create": True, "inspect": True, "restore": True, "profileAware": True, "autoDetectArtifactKind": True},
            "checkpoint": True,
            "workspaceSnapshot": {"create": not profile.development, "inspect": True, "restore": not profile.development},
            "release": profile.development,
            "declarativeUpdate": {
                "available": profile.development,
                "format": "agent-devtools-update-scenario",
                "formatVersion": 1,
                "stdlibOnly": True,
                "arbitraryHooks": False,
                "isolatedPreparation": True,
                "provenanceSha256": True,
                "technicalWorkspace": ".agent-updates/",
                "trackedScenarios": False,
                "scenarioSnapshotPerRun": True,
                "contractTransitions": True,
                "contractConstantsOwnedByScenario": True,
                "staleExactAssertionGuard": True,
                "indirectContractAssertionGuard": True,
                "autoChain": True,
                "freshProcessPerEdge": True,
                "periodicFreshAutoProcess": True,
                "periodicForeground": True,
                "periodicDaemon": False,
                "failedCatalogSuppression": True,
                "incomingDirectory": ".agent-updates/incoming/",
                "appliedDirectory": ".agent-updates/applied/",
            },
        },
        "exitCodeSemantics": {
            "0": "successful or clean action",
            "1": "expected actionable-gate or non-clean review state for commands that document it, for example cognition checkpoint with required promotions; not necessarily execution failure",
            "2": "invalid invocation, violated command contract, or execution-contract failure",
        },
        "normalSurface": {
            "profile": profile.profile_id,
            "commands": (
                ["agent begin", "agent cognition", "agent verify research", "agent cognition checkpoint", "agent work complete"]
                if profile.profile_id == "research"
                else ["agent begin", "agent cognition", "agent verify", "agent cognition checkpoint", "agent work complete"]
            ),
            "advancedPrimitives": ["agent task update", "agent work enter", "agent work finish", "agent knowledge promote", "agent checkpoint create", "agent resume"],
            "optionalReadOnly": ["agent work report"],
            "diagnostics": ["agent workflow show", "agent capabilities --json", "agent --help"],
            "diagnosticsAreRoutine": False,
            "advancedPrimitivesAreRoutine": False,
        },
        "tracked": ["AGENTS.md", "agent-tools.json", "agent-check.policy.json", ".agent-knowledge/"],
        "disposable": ["devtools/agent/", ".agent-cache/", ".agent-work/", ".agent-updates/", ".agent-bootstrap-report.json"],
    }
