from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agent_devtools.core.diagnostics import unique_diagnostics
from agent_devtools.core.process import ProcessResult

SUPPORTED_ADAPTERS = {"exit-code", "unittest", "pytest", "vite", "json-line"}


@dataclass(frozen=True)
class SuiteEvaluation:
    accepted: bool
    result: dict[str, Any]
    diagnostics: tuple[str, ...] = ()


def _iter_lines(result: ProcessResult):
    if result.log_path.is_file():
        with result.log_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                yield line.rstrip("\n")
        return
    yield from result.output_tail.splitlines()


def _scan_last_int(result: ProcessResult, pattern: str) -> int | None:
    rx = re.compile(pattern)
    value: int | None = None
    for line in _iter_lines(result):
        match = rx.search(line)
        if match:
            try:
                value = int(match.group(1))
            except (TypeError, ValueError):
                pass
    return value


def _evaluate_exit_code(result: ProcessResult, _root: Path, _options: Mapping[str, Any]) -> SuiteEvaluation:
    accepted = result.returncode == 0
    diagnostics = () if accepted else tuple(unique_diagnostics(result.output_tail))
    return SuiteEvaluation(accepted, {"exitCode": result.returncode}, diagnostics)


def _evaluate_unittest(result: ProcessResult, _root: Path, options: Mapping[str, Any]) -> SuiteEvaluation:
    tests = _scan_last_int(result, r"Ran\s+(\d+)\s+tests?")
    accepted = result.returncode == 0 and (tests is not None or not bool(options.get("requireCount", False)))
    payload: dict[str, Any] = {"exitCode": result.returncode, "tests": tests}
    diagnostics = () if accepted else tuple(unique_diagnostics(result.output_tail))
    return SuiteEvaluation(accepted, payload, diagnostics)


def _evaluate_pytest(result: ProcessResult, _root: Path, options: Mapping[str, Any]) -> SuiteEvaluation:
    counts = {
        "passed": _scan_last_int(result, r"(?:^|\s)(\d+)\s+passed\b"),
        "failed": _scan_last_int(result, r"(?:^|\s)(\d+)\s+failed\b"),
        "errors": _scan_last_int(result, r"(?:^|\s)(\d+)\s+errors?\b"),
        "skipped": _scan_last_int(result, r"(?:^|\s)(\d+)\s+skipped\b"),
    }
    require_summary = bool(options.get("requireSummary", False))
    summary_seen = any(value is not None for value in counts.values())
    accepted = result.returncode == 0 and (summary_seen or not require_summary)
    payload = {"exitCode": result.returncode, **counts}
    diagnostics = () if accepted else tuple(unique_diagnostics(result.output_tail))
    return SuiteEvaluation(accepted, payload, diagnostics)


def _evaluate_vite(result: ProcessResult, root: Path, options: Mapping[str, Any]) -> SuiteEvaluation:
    modules = _scan_last_int(result, r"(\d+)\s+modules transformed")
    required_files = [str(item) for item in options.get("requiredFiles", []) if str(item).strip()]
    missing = [rel for rel in required_files if not (root / rel).is_file()]
    require_modules = bool(options.get("requireModules", False))
    accepted = result.returncode == 0 and not missing and (modules is not None or not require_modules)
    payload = {
        "exitCode": result.returncode,
        "modules": modules,
        "requiredFiles": required_files,
        "missingFiles": missing,
    }
    diagnostics: list[str] = []
    if missing:
        diagnostics.append("missing required output: " + ", ".join(missing))
    if require_modules and modules is None:
        diagnostics.append("Vite module count was not found in output")
    if result.returncode != 0:
        diagnostics.extend(unique_diagnostics(result.output_tail))
    return SuiteEvaluation(accepted, payload, tuple(dict.fromkeys(diagnostics)))


def _evaluate_json_line(result: ProcessResult, _root: Path, options: Mapping[str, Any]) -> SuiteEvaluation:
    prefix = str(options.get("prefix") or "AGENT_CHECK_RESULT ")
    required_format = str(options.get("format") or "").strip() or None
    normal_re = str(options.get("normalCompletionRegex") or "").strip() or None
    fatal_re = str(options.get("fatalRegex") or "").strip() or None
    failures_field = str(options.get("failuresField") or "failures")
    passed_field = str(options.get("passedField") or "passed")
    failed_field = str(options.get("failedField") or "failed")
    allowed_failures = {str(item) for item in options.get("allowedFailures", [])}
    allow_nonzero = bool(options.get("allowNonZeroWithAllowedFailures", bool(allowed_failures)))

    payload: dict[str, Any] | None = None
    normal_rx = re.compile(normal_re) if normal_re is not None else None
    fatal_rx = re.compile(fatal_re, flags=re.IGNORECASE) if fatal_re is not None else None
    completed_normally = normal_rx is None
    fatal_output = False
    for line in _iter_lines(result):
        if normal_rx is not None and normal_rx.search(line):
            completed_normally = True
        if fatal_rx is not None and fatal_rx.search(line):
            fatal_output = True
        if not line.startswith(prefix):
            continue
        try:
            candidate = json.loads(line[len(prefix):].strip())
        except json.JSONDecodeError:
            continue
        if not isinstance(candidate, dict):
            continue
        if required_format is not None and candidate.get("format") != required_format:
            continue
        payload = candidate
    if payload is None:
        diagnostics = tuple(unique_diagnostics(result.output_tail)) or ("structured result line not found",)
        return SuiteEvaluation(False, {
            "exitCode": result.returncode,
            "structuredResult": False,
            "completedNormally": completed_normally,
            "fatalOutput": fatal_output,
        }, diagnostics)

    failures = [str(item) for item in (payload.get(failures_field) or [])]
    try:
        passed = int(payload.get(passed_field) or 0)
    except (TypeError, ValueError):
        passed = 0
    try:
        failed = int(payload.get(failed_field) or len(failures))
    except (TypeError, ValueError):
        failed = len(failures)

    accepted = False
    if completed_normally and not fatal_output:
        if result.returncode == 0:
            accepted = not failures and failed == 0
        elif allow_nonzero and failures:
            accepted = all(label in allowed_failures for label in failures)

    unexpected = [label for label in failures if label not in allowed_failures]
    expected = [label for label in failures if label in allowed_failures]
    summary: dict[str, Any] = {
        "exitCode": result.returncode,
        "structuredResult": True,
        "completedNormally": completed_normally,
        "fatalOutput": fatal_output,
        "passed": passed,
        "failed": failed,
        "failures": failures,
        "expectedFailures": expected,
        "unexpectedFailures": unexpected,
    }
    for key in ("groups", "selectedGroups", "skippedGroups"):
        if key in payload:
            summary[key] = payload[key]

    diagnostics: list[str] = []
    if not completed_normally:
        diagnostics.append("suite did not reach its normal completion marker")
    if fatal_output:
        diagnostics.append("fatal output marker detected")
    if unexpected:
        diagnostics.append("unexpected failures: " + ", ".join(unexpected[:8]))
    if not accepted and not diagnostics:
        diagnostics.extend(unique_diagnostics(result.output_tail))
    return SuiteEvaluation(accepted, summary, tuple(dict.fromkeys(diagnostics)))


def evaluate_suite_result(
    result: ProcessResult,
    *,
    root: Path,
    kind: str,
    options: Mapping[str, Any] | None = None,
) -> SuiteEvaluation:
    options = options or {}
    if kind == "exit-code":
        return _evaluate_exit_code(result, root, options)
    if kind == "unittest":
        return _evaluate_unittest(result, root, options)
    if kind == "pytest":
        return _evaluate_pytest(result, root, options)
    if kind == "vite":
        return _evaluate_vite(result, root, options)
    if kind == "json-line":
        return _evaluate_json_line(result, root, options)
    raise ValueError(f"Unsupported suite result adapter: {kind}")
