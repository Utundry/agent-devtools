from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import __version__
from .profiles import load_profile

CLI_CONTRACT_VERSION = 23
WORKFLOW_CONTRACT_VERSION = 17


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
            "Read relevant durable knowledge when needed; promote only new reusable knowledge and reuse exact existing statements instead of creating duplicates.",
            "AGENTS.md defines the mandatory project workflow; the CLI is authoritative for current syntax and capabilities.",
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
            "Active context is a deterministic budgeted projection, not a second knowledge store: rank by task/stage/scope/path/terms, suppress near-duplicates, explain every selection, and exclude superseded/historical knowledge unless explicitly requested.",
            "Interactive shell is UX only: every shell action normalizes to the ordinary CLI dispatcher, creates no shell-only project state, and remains reproducible as a conventional command.",
        ],
        "routineRoute": {
            "name": "Agent Work Lifecycle",
            "canonicalRoutine": ["begin", "work", "checkpoint", "complete"],
            "entry": "begin",
            "entryPrimitive": "work enter",
            "entryRule": "Routine agents use begin; work enter is a lower-level primitive.",
            "execution": "Use project-native tools; record only meaningful new findings or decisions.",
            "completion": "work complete",
            "alreadyVerifiedCompletion": "work finish",
            "automaticVerificationReuse": True,
            "explicitExecution": "work complete --no-cache",
            "deepVerification": "Explicit certification, replay or release; not part of routine completion.",
            "contextIndex": "Optional; created only by explicit context operations, reused by briefings when present.",
            "knowledgePromotion": "Event-driven; checkpoint is read-only by default and prints executable next actions. checkpoint --promote-required is the explicit convenience path for required decisions/requirements only. Zero new records is valid when no durable candidate exists.",
            "knowledgeLifecycle": "Tracked .agent-knowledge JSON is canonical durable state; supersession is relation-derived and append-friendly.",
            "contextProjection": "Budgeted stage-aware projection from active durable knowledge with compact cues, expand refs and deterministic selection reasons; runtime usage state is disposable.",
            "interactiveShell": "Optional UX over the same top-level CLI dispatcher; no shell-only state or write path.",
            "semanticJournal": "Append-only disposable SQLite session journal; cognition checkpoint classifies durable candidates before closeout.",
            "semanticCapture": "Use cognition with positional text or --text; add --subject only for reusable semantics, then let checkpoint classify required/advisory/session-only outcomes.",
            "directKnowledgePromotion": "expert primitive only; routine agents do not call knowledge promote before checkpoint classification.",
            "researchVerification": "Research routine uses verify research guided review, then explicit compact or granular attestation.",
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
            "workflow": {"show": True, "validate": True},
            "capabilities": {"json": True},
            "selfUpdate": {"available": True, "latestDiscovery": True, "explicitVersion": True, "checkOnly": True, "freshDownload": True, "identityVerified": True},
            "begin": {"available": True, "canonicalRoutineEntry": True, "usesWorkEnter": True, "durableContextProjection": True, "knowledgeHealth": True, "newStateModel": False},
            "work": {"enter": True, "start": True, "align": True, "status": True, "complete": True, "finish": True, "oneActionEntry": True, "handoffEntry": True, "routineEntryFastPath": True, "alignmentPendingOptOut": True, "oneActionCompletion": True, "automaticVerificationReuse": True, "baselineWhenUnverified": True, "taskGapGate": True, "noGapFastPath": True, "semanticCloseout": True},
            "cognition": {"observation": True, "decision": True, "finding": True, "assumption": True, "requirement": not profile.development, "openQuestion": not profile.development, "evidence": not profile.development, "blocker": True, "resolveBlocker": True, "status": True, "checkpoint": True, "checkpointPromoteRequired": True, "textOptionAlias": True, "checkpointSessionOnlyClassification": True, "appendOnlyJournal": True},
            "knowledge": {"promote": True, "remember": True, "why": True, "lifecycle": True, "supersede": True, "validate": True, "status": True, "researchKinds": not profile.development, "softContradictionWarnings": True, "promotionRequired": False, "routineDirectPromote": False, "checkpointAuthority": True, "canonicalStore": ".agent-knowledge", "durableSqlite": False},
            "source": {"add": not profile.development, "list": not profile.development},
            "context": {"available": True, "affected": profile.development, "prepare": True, "current": True, "why": True, "expand": True, "stageAware": True, "hierarchicalScope": True, "budgeted": True, "deduplicate": True, "selectionReasons": True, "usageSqliteDisposable": True},
            "shell": {"available": True, "interactive": True, "batchCommand": True, "sameDispatcher": True, "shellOnlyState": False},
            "changes": {"status": profile.development, "patch": profile.development, "canonicalUntracked": profile.development, "workspaceLocalMarks": profile.development},
            "verification": {
                "check": profile.verification_mode == "check",
                "guidedResearchReview": profile.profile_id == "research",
                "researchConfirmAllPass": profile.profile_id == "research",
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
        },
        "tracked": ["AGENTS.md", "agent-tools.json", "agent-check.policy.json", ".agent-knowledge/"],
        "disposable": ["devtools/agent/", ".agent-cache/", ".agent-work/", ".agent-bootstrap-report.json"],
    }
