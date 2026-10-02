from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Iterable

from agent_devtools.core.io import atomic_json_write
from agent_devtools.core.workspace import default_work_root
from .state import utc_now

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


def record_verification(root: Path, *, label: str, status: str, evidence: Iterable[str] = (), summary: str = "") -> dict[str, Any]:
    label = label.strip()
    status = status.strip().lower()
    if not label:
        raise VerificationError("verification label is required")
    if status not in {"pass", "fail"}:
        raise VerificationError("verification status must be pass or fail")
    log = _load(root)
    record = {
        "id": uuid.uuid4().hex,
        "label": label,
        "status": status,
        "evidence": [str(item).strip() for item in evidence if str(item).strip()],
        "summary": summary.strip(),
        "verificationKind": "attestation",
        "completedAtUtc": utc_now(),
    }
    log["records"].append(record)
    atomic_json_write(verification_path(root), log)
    return record


def latest_verification(root: Path) -> dict[str, Any] | None:
    records = _load(root).get("records", [])
    return dict(records[-1]) if records else None


def verification_status(root: Path) -> dict[str, Any]:
    log = _load(root)
    return {"count": len(log["records"]), "latest": dict(log["records"][-1]) if log["records"] else None}


RESEARCH_CHECKS = ("arithmetic", "sourcing", "assumptions", "knowledge", "unresolved_questions")

def record_research_bundle(root: Path, *, checks: dict[str, str], evidence: Iterable[str] = (), summary: str = "") -> dict[str, Any]:
    normalized: dict[str, str] = {}
    for name in RESEARCH_CHECKS:
        value = str(checks.get(name) or "").strip().lower()
        if value not in {"pass", "warn", "fail"}:
            raise VerificationError(f"research verification {name} must be pass, warn or fail")
        normalized[name] = value
    overall = "fail" if "fail" in normalized.values() else "pass"
    record = record_verification(root, label="research-bundle", status=overall, evidence=evidence, summary=summary)
    log = _load(root)
    for item in reversed(log["records"]):
        if item.get("id") == record["id"]:
            item["checks"] = normalized
            break
    atomic_json_write(verification_path(root), log)
    return {**record, "checks": normalized}


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
    record = record_verification(
        root, label="check", status=report["status"], evidence=[evidence],
        summary="Project-native check report; suite results, cache results and log paths are retained in the report.",
    )
    record["verificationKind"] = "machine-check"
    record["reportSha256"] = sha256_file(report_path)
    record["checkCompletedAtUtc"] = report.get("completedAtUtc")
    log = _load(root)
    log["records"][-1] = record
    atomic_json_write(verification_path(root), log)
    return record
