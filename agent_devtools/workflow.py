from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import __version__
from .profiles import load_profile

CLI_CONTRACT_VERSION = 8
WORKFLOW_CONTRACT_VERSION = 4


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
            "AGENTS.md defines the mandatory project workflow; the CLI is authoritative for current syntax and capabilities.",
            "Agent DevTools orchestrates project-native tools; it does not replace Git, build, test, release, or domain tooling.",
            "Canonical project change discovery includes untracked files governed by the source contract; plain git diff is not a complete handoff inventory.",
            "Session state and caches are disposable; durable project knowledge is tracked in .agent-knowledge/.",
            "Autonomy starts after alignment: ask only about material gaps that are expensive to get wrong; propose a concrete best option and obtain explicit user approval before substantial execution when such gaps exist. If the task is already sufficiently specified, record the no-gap fast path and proceed without a user turn.",
            "Alignment must be explicit in state, not necessarily visible as friction to the user. Never ask a question merely to satisfy the gate.",
        ],
        "phases": [
            {
                "id": "orient",
                "required": True,
                "purpose": "Recover project/task context and existing durable knowledge before substantial work.",
                "commands": ["resume", "work status", "knowledge status"],
            },
            {
                "id": "start",
                "required": True,
                "purpose": "Start or continue an explicit work session with a goal and next action.",
                "commands": ["work start"],
            },
            {
                "id": "align",
                "required": True,
                "purpose": "Resolve material task gaps before substantial execution. When none exist, use the frictionless no-gap fast path and continue immediately without asking the user; when material gaps do exist, propose concrete options and obtain explicit approval.",
                "commands": ["work align"],
            },
            {
                "id": "work",
                "required": True,
                "purpose": "Record meaningful findings, decisions, assumptions, and blockers while changing project artifacts.",
                "commands": ["cognition", "context"],
            },
            {
                "id": "verify",
                "required": True,
                "purpose": "Collect fresh profile-appropriate verification evidence using project-native tooling.",
                "commands": ["check", "verify", "changes status"],
            },
            {
                "id": "knowledge",
                "required": False,
                "purpose": "Promote only lasting knowledge and reconcile contradictions explicitly.",
                "commands": ["knowledge promote", "knowledge validate"],
            },
            {
                "id": "finish",
                "required": True,
                "purpose": "Prefer one-action completion: validate gates and durable knowledge, run deterministic verification when available, then finish only on fresh PASS evidence.",
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
            "workflow": {"show": True},
            "capabilities": {"json": True},
            "selfUpdate": {"available": True, "latestDiscovery": True, "explicitVersion": True, "checkOnly": True, "freshDownload": True, "identityVerified": True},
            "work": {"start": True, "align": True, "status": True, "complete": True, "finish": True, "oneActionCompletion": True, "taskGapGate": True, "noGapFastPath": True},
            "cognition": {"decision": True, "finding": True, "assumption": True, "requirement": not profile.development, "openQuestion": not profile.development, "evidence": not profile.development, "blocker": True, "resolveBlocker": True, "status": True},
            "knowledge": {"promote": True, "validate": True, "status": True, "researchKinds": not profile.development, "softContradictionWarnings": True, "promotionRequired": False},
            "source": {"add": not profile.development, "list": not profile.development},
            "context": {"available": True, "affected": profile.development},
            "changes": {"status": profile.development, "patch": profile.development, "canonicalUntracked": profile.development},
            "verification": {
                "check": profile.verification_mode == "check",
                "record": profile.verification_mode == "record",
                "researchBundle": profile.profile_id == "research",
                "resumableChunks": profile.verification_mode == "check",
                "idleWatchdog": profile.verification_mode == "check",
                "hardWatchdog": profile.verification_mode == "check",
            },
            "resume": True,
            "checkpoint": True,
            "workspaceSnapshot": {"create": not profile.development, "inspect": True, "restore": not profile.development},
            "release": profile.development,
        },
        "tracked": ["AGENTS.md", "agent-tools.json", "agent-check.policy.json", ".agent-knowledge/"],
        "disposable": ["devtools/agent/", ".agent-cache/", ".agent-work/", ".agent-bootstrap-report.json"],
    }
