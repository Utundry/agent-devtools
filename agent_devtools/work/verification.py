from __future__ import annotations

import hashlib
import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from agent_devtools.core.hashing import stable_fingerprint
from agent_devtools.core.io import atomic_json_write
from agent_devtools.core.workspace import default_work_root
from .state import load_task_state, update_task, utc_now

VERIFY_FORMAT = "agent-devtools-verification-log"
VERIFY_VERSION = 1


class VerificationError(RuntimeError):
    pass


def verification_path(root: Path) -> Path:
    return default_work_root(root) / "verification.json"


def _load(root: Path) -> dict[str, Any]:
    path = verification_path(root)
    if not path.is_file():
        return {"format": VERIFY_FORMAT, "formatVersion": VERIFY_VERSION, "records": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read verification log: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("format") != VERIFY_FORMAT or int(raw.get("formatVersion") or 0) != VERIFY_VERSION:
        raise VerificationError("unsupported verification log format/version")
    records = raw.get("records")
    if not isinstance(records, list):
        raise VerificationError("verification records must be an array")
    return raw


def verification_record(root: Path, record_id: str) -> dict[str, Any]:
    record_id = str(record_id or "").strip()
    if not record_id:
        raise VerificationError("verification record id is required")
    for record in _load(root)["records"]:
        if str(record.get("id") or "") == record_id:
            return dict(record)
    raise VerificationError(f"verification record not found: {record_id}")


def record_verification(
    root: Path,
    *,
    label: str,
    status: str,
    evidence: Iterable[str] = (),
    summary: str = "",
    verification_kind: str = "attestation",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    label = label.strip()
    status = status.strip().lower()
    verification_kind = verification_kind.strip()
    if not label:
        raise VerificationError("verification label is required")
    if status not in {"pass", "fail"}:
        raise VerificationError("verification status must be pass or fail")
    if not verification_kind:
        raise VerificationError("verification kind is required")
    log = _load(root)
    record = {
        "id": uuid.uuid4().hex,
        "label": label,
        "status": status,
        "evidence": [str(item).strip() for item in evidence if str(item).strip()],
        "summary": summary.strip(),
        "verificationKind": verification_kind,
        "completedAtUtc": utc_now(),
    }
    if details:
        overlap = set(record) & set(details)
        if overlap:
            raise VerificationError(
                "verification details cannot replace core fields: " + ", ".join(sorted(overlap))
            )
        record.update(details)
    previous_records = list(log["records"])
    log["records"].append(record)
    atomic_json_write(verification_path(root), log)
    state = load_task_state(root)
    if state is not None:
        from .journal import SemanticJournalError, append_event
        source = "verify.run" if verification_kind == "executed-command" else "verify.record"
        try:
            append_event(
                root,
                task_id=str(state.get("taskId") or ""),
                kind="verification",
                text=f"{label}: {status}",
                metadata={
                    "source": source,
                    "recordId": record["id"],
                    "verificationKind": verification_kind,
                    "status": record["status"],
                    "evidence": record["evidence"],
                    "summary": record["summary"],
                    "fallbackForFailureFingerprint": (
                        str((record.get("fallbackFor") or {}).get("fingerprint") or "")
                        if isinstance(record.get("fallbackFor"), dict)
                        else ""
                    ),
                },
                created_at_utc=record["completedAtUtc"],
            )
        except SemanticJournalError as exc:
            log["records"] = previous_records
            atomic_json_write(verification_path(root), log)
            raise VerificationError(f"cannot persist semantic journal; verification record rolled back: {exc}") from exc
    return record


def _output_preview(data: bytes, limit: int) -> str:
    text = data.decode("utf-8", errors="replace").strip()
    if len(text) <= limit:
        return text
    if limit <= 0:
        return f"… <truncated {len(text)} chars>"
    head_chars = (limit + 1) // 2
    tail_chars = limit // 2
    omitted = len(text) - head_chars - tail_chars
    head = text[:head_chars].rstrip()
    tail = text[-tail_chars:].lstrip() if tail_chars else ""
    marker = f"… <truncated {omitted} chars> …"
    return f"{head}\n{marker}\n{tail}" if tail else f"{head}\n{marker}"


def _verification_source_state(root: Path) -> dict[str, Any]:
    """Content-bearing source state for verification evidence.

    This deliberately does not reuse the briefing metadata fingerprint: verification
    freshness needs file-content evidence, not only changed-path metadata.
    """
    try:
        from agent_devtools.changes import ChangeSetError, discover_changes
        report = discover_changes(root)
    except (OSError, ValueError, ChangeSetError):
        from .source_snapshot import source_snapshot
        return source_snapshot(root)
    if report.get("available") is False:
        # Source tree without Git (for example a ZIP prototype): preserve
        # file-content freshness evidence without inventing a change list.
        from .source_snapshot import source_snapshot
        return source_snapshot(root)

    rows: list[dict[str, Any]] = []
    for item in report.get("entries", []):
        if not isinstance(item, dict) or not item.get("canonical"):
            continue
        rel = str(item.get("path") or "").replace("\\", "/")
        status = str(item.get("status") or "")
        path = root / rel
        if status == "deleted" or not path.is_file():
            digest = None
        else:
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as exc:
                return {"available": False, "reason": f"cannot hash {rel}: {exc}"}
        rows.append({"path": rel, "status": status, "sha256": digest})

    rows.sort(key=lambda item: item["path"])
    payload = {
        "gitHead": str(report.get("head") or ""),
        "files": rows,
    }
    return {
        "available": True,
        "gitHead": payload["gitHead"],
        "changedFiles": [item["path"] for item in rows],
        "files": rows,
        "fingerprint": stable_fingerprint(payload),
    }


def _configured_command_relevance(root: Path, argv: list[str], source_state: dict[str, Any]) -> dict[str, Any]:
    """Conservatively bind an exact configured static suite command to affected policy."""
    if source_state.get("available") is False:
        return {"status": "unknown", "reason": "source state unavailable"}
    if source_state.get("mode") == "directory-snapshot":
        return {"status": "unknown", "reason": "source snapshot verified, but changed paths are unavailable without Git"}
    try:
        from agent_devtools.check.config import CheckConfigError, load_check_config
        from agent_devtools.check.policy import PolicyError, load_policy
        config = load_check_config(root)
        policy = load_policy(config.policy_path)
    except (OSError, ValueError, CheckConfigError, PolicyError) as exc:
        return {"status": "unknown", "reason": f"check policy unavailable: {exc}"}

    matches: list[str] = []
    for suite, spec in config.commands.items():
        configured = list(spec.argv)
        # Placeholder commands need the check runner's expansion context. Refuse to
        # guess rather than treating a superficially similar argv as proof.
        if any("{" in token or "}" in token for token in configured):
            continue
        if str(spec.cwd or ".").replace("\\", "/") not in {"", "."}:
            continue
        if configured == argv:
            matches.append(str(suite))

    if len(matches) != 1:
        return {
            "status": "unknown",
            "reason": (
                "command does not exactly match one static configured suite"
                if not matches else
                "command matches multiple configured suites"
            ),
            "matchedSuites": matches,
        }

    suite = matches[0]
    changed = source_state.get("changedFiles") or []
    try:
        plan = policy.plan("affected", changed)
    except PolicyError as exc:
        return {"status": "unknown", "reason": f"affected policy unavailable: {exc}", "suite": suite}
    selected = {str(item.get("id")) for item in plan.selected}
    relevant = suite in selected
    return {
        "status": "relevant" if relevant else "unrelated",
        "reason": "suite selected by affected policy" if relevant else "suite not selected by affected policy",
        "suite": suite,
        "changedFiles": list(plan.changed_files),
        "effectiveImpact": list(plan.effective_impact),
        "selectedSuites": [str(item.get("id")) for item in plan.selected],
        "fallbackFull": bool(plan.fallback_full),
        "fallbackReasons": list(plan.fallback_reasons),
    }


def assess_verification_record(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    item = dict(record)
    binding = item.get("sourceBinding")
    if not isinstance(binding, dict) or not binding.get("afterFingerprint"):
        item["sourceFreshness"] = "unknown"
        return item
    current = _verification_source_state(root)
    item["currentSourceState"] = current
    if not current.get("available"):
        item["sourceFreshness"] = "unknown"
        return item
    item["sourceFreshness"] = (
        "current"
        if str(current.get("fingerprint") or "") == str(binding.get("afterFingerprint") or "")
        else "stale"
    )
    return item


def _fallback_failure_context(root: Path, fingerprint: str) -> dict[str, Any] | None:
    fingerprint = str(fingerprint or "").strip()
    if not fingerprint:
        return None
    state = load_task_state(root)
    if state is None:
        raise VerificationError("--fallback-for requires an active task with recorded failure provenance")
    from .journal import events_for_task
    matches = []
    for event in events_for_task(root, str(state.get("taskId") or "")):
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        if (
            str(metadata.get("source") or "") == "cognition.tool-failure"
            and str(metadata.get("fingerprint") or "") == fingerprint
        ):
            matches.append((event, metadata))
    if not matches:
        raise VerificationError(
            f"--fallback-for does not match a recorded tool failure in the current task: {fingerprint}"
        )
    event, metadata = matches[-1]
    return {
        "fingerprint": fingerprint,
        "failureEventId": event.get("id"),
        "failureKind": metadata.get("failureKind") or "unknown",
        "tool": metadata.get("tool"),
        "operation": metadata.get("operation"),
        "error": metadata.get("error"),
        "verificationId": metadata.get("verificationId"),
    }


def _resolve_fallback_blocker(root: Path, fingerprint: str) -> str | None:
    state = load_task_state(root)
    if state is None:
        return None
    marker = f"[{fingerprint}]"
    blockers = [str(item) for item in state.get("blockers", []) if marker in str(item)]
    if len(blockers) > 1:
        raise VerificationError(
            f"successful fallback matched multiple blockers for failure fingerprint {fingerprint}"
        )
    if not blockers:
        return None
    update_task(
        root,
        resolve_blockers=blockers,
        semantic_source="verify.fallback",
    )
    return blockers[0]


def run_verification_command(
    root: Path,
    *,
    command: Iterable[str],
    label: str = "command",
    summary: str = "",
    timeout: float | None = None,
    output_limit: int = 1600,
    fallback_for: str = "",
) -> dict[str, Any]:
    argv = [str(item) for item in command]
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        raise VerificationError("verify run requires a command after `--`")
    if timeout is not None and timeout <= 0:
        raise VerificationError("verify run timeout must be greater than zero")
    if output_limit < 0:
        raise VerificationError("verify run output limit cannot be negative")
    fallback_context = _fallback_failure_context(root, fallback_for)

    source_before = _verification_source_state(root)
    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            argv,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        exit_code: int | None = int(completed.returncode)
        stdout = bytes(completed.stdout or b"")
        stderr = bytes(completed.stderr or b"")
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = None
        stdout = exc.stdout if isinstance(exc.stdout, bytes) else str(exc.stdout or "").encode("utf-8")
        stderr = exc.stderr if isinstance(exc.stderr, bytes) else str(exc.stderr or "").encode("utf-8")
    except OSError as exc:
        raise VerificationError(f"cannot execute verification command: {exc}") from exc

    duration_ms = round((time.monotonic() - started) * 1000.0, 3)
    source_after = _verification_source_state(root)
    before_fp = str(source_before.get("fingerprint") or "") if source_before.get("available") else ""
    after_fp = str(source_after.get("fingerprint") or "") if source_after.get("available") else ""
    source_changed_during_execution = bool(before_fp and after_fp and before_fp != after_fp)
    relevance = _configured_command_relevance(root, argv, source_after)
    status = "pass" if exit_code == 0 and not timed_out else "fail"
    stdout_sha256 = hashlib.sha256(stdout).hexdigest()
    stderr_sha256 = hashlib.sha256(stderr).hexdigest()
    details = {
        "command": argv,
        "cwd": ".",
        "exitCode": exit_code,
        "timedOut": timed_out,
        "timeoutSeconds": timeout,
        "durationMs": duration_ms,
        "stdoutBytes": len(stdout),
        "stderrBytes": len(stderr),
        "stdoutSha256": stdout_sha256,
        "stderrSha256": stderr_sha256,
        "stdoutPreview": _output_preview(stdout, output_limit),
        "stderrPreview": _output_preview(stderr, output_limit),
        "sourceBinding": {
            "beforeFingerprint": before_fp or None,
            "afterFingerprint": after_fp or None,
            "changedDuringExecution": source_changed_during_execution,
            "state": source_after,
        },
        "relevance": relevance,
    }
    if fallback_context is not None:
        details["fallbackFor"] = fallback_context
    exit_evidence = "timeout" if timed_out else str(exit_code)
    record = record_verification(
        root,
        label=label,
        status=status,
        evidence=[
            f"command-exit:{exit_evidence}",
            f"stdout-sha256:{stdout_sha256}",
            f"stderr-sha256:{stderr_sha256}",
        ],
        summary=summary or "Command executed by Agent DevTools; full output represented by SHA-256 digests.",
        verification_kind="executed-command",
        details=details,
    )
    if status == "pass" and fallback_context is not None:
        resolved = _resolve_fallback_blocker(root, str(fallback_context["fingerprint"]))
        record["fallbackResolution"] = {
            "status": "resolved" if resolved else "not-blocking",
            "blocker": resolved,
        }
    return record


def latest_verification(root: Path) -> dict[str, Any] | None:
    records = _load(root).get("records", [])
    return assess_verification_record(root, records[-1]) if records else None


def verification_status(root: Path) -> dict[str, Any]:
    log = _load(root)
    return {
        "count": len(log["records"]),
        "latest": assess_verification_record(root, log["records"][-1]) if log["records"] else None,
    }


def verification_history(root: Path, *, limit: int = 5) -> dict[str, Any]:
    if limit < 1:
        raise VerificationError("verification history limit must be at least 1")
    records = _load(root)["records"]
    selected = [assess_verification_record(root, item) for item in reversed(records[-limit:])]
    return {
        "count": len(records),
        "shown": len(selected),
        "records": selected,
    }


RESEARCH_CHECKS = ("arithmetic", "sourcing", "assumptions", "knowledge", "unresolved_questions")

def record_research_bundle(root: Path, *, checks: dict[str, str], evidence: Iterable[str] = (), summary: str = "") -> dict[str, Any]:
    normalized: dict[str, str] = {}
    for name in RESEARCH_CHECKS:
        value = str(checks.get(name) or "").strip().lower()
        if value not in {"pass", "warn", "fail"}:
            raise VerificationError(f"research verification {name} must be pass, warn or fail")
        normalized[name] = value
    assessment_status = (
        "fail" if "fail" in normalized.values()
        else "warn" if "warn" in normalized.values()
        else "pass"
    )
    gate_status = "fail" if assessment_status == "fail" else "pass"
    warning_checks = [name for name in RESEARCH_CHECKS if normalized[name] == "warn"]
    record = record_verification(root, label="research-bundle", status=gate_status, evidence=evidence, summary=summary)
    log = _load(root)
    for item in reversed(log["records"]):
        if item.get("id") == record["id"]:
            item["checks"] = normalized
            item["assessmentStatus"] = assessment_status
            item["warningChecks"] = warning_checks
            item["completionEligible"] = gate_status == "pass"
            break
    atomic_json_write(verification_path(root), log)
    return {
        **record,
        "checks": normalized,
        "assessmentStatus": assessment_status,
        "warningChecks": warning_checks,
        "completionEligible": gate_status == "pass",
    }


def record_check_report(root: Path, report: dict[str, Any]) -> dict[str, Any] | None:
    """Bind completed CLI checks to their inspectable report and report hash."""
    if report.get("status") not in {"pass", "fail"}:
        return None
    from agent_devtools.core.hashing import sha256_file
    report_path = Path(report["runDirectory"]) / "report.json"
    if not report_path.is_absolute():
        report_path = root / report_path
    report_path = report_path.resolve()
    try:
        evidence = report_path.relative_to(root.resolve()).as_posix()
    except ValueError:
        evidence = str(report_path)
    return record_verification(
        root,
        label="check",
        status=report["status"],
        evidence=[evidence],
        summary="Project-native check report; suite results, cache results and log paths are retained in the report.",
        verification_kind="machine-check",
        details={
            "reportSha256": sha256_file(report_path),
            "checkCompletedAtUtc": report.get("completedAtUtc"),
        },
    )
