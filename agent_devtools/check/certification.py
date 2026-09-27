from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_devtools.core.evidence import CertifiedEvidenceStore
from agent_devtools.core.hashing import sha256_file, stable_fingerprint
from agent_devtools.core.identity import engine_fingerprint
from agent_devtools.core.process import ManagedProcessRunner
from agent_devtools.core.tools import tool_identity
from agent_devtools.core.workspace import RunWorkspace, WorkspaceError, utc_now
import time

from .adapters import evaluate_suite_result
from .cache import matching_input_hashes
from .config import CheckConfig, SuiteCommand
from .contracts import cached_outputs_match, evaluate_outputs, missing_required
from .policy import SelectionPlan
from .runner import (
    CheckRunError,
    _execution_fingerprint,
    _file_set_paths,
    _resolved_execution,
    _resume_key,
    _compatible_resume_chunks,
    _execute_chunks,
)

CERTIFIED_SUITE_SCHEMA = "agent-devtools-certified-suite-v1"
CERTIFIED_FILE_SCHEMA = "agent-devtools-certified-file-v1"


def _source_fingerprint(global_hashes: dict[str, str], input_hashes: dict[str, str]) -> str:
    return stable_fingerprint({"globalInputs": global_hashes, "inputs": input_hashes})


def _suite_input_fingerprint(
    *,
    root: Path,
    spec: SuiteCommand,
    plan: SelectionPlan,
    input_hashes: dict[str, str],
    global_hashes: dict[str, str],
) -> str:
    argv, cwd, _env, env_overrides = _resolved_execution(root, spec, plan)
    return stable_fingerprint({
        "schema": CERTIFIED_SUITE_SCHEMA,
        "engineFingerprint": engine_fingerprint(),
        "suite": spec.suite,
        "argv": list(argv),
        "tool": tool_identity(spec.tool) if spec.tool else None,
        "inputs": input_hashes,
        "globalInputs": global_hashes,
        "execution": _execution_fingerprint(root, spec, cwd, env_overrides),
    })


def _file_input_fingerprint(
    *,
    root: Path,
    spec: SuiteCommand,
    plan: SelectionPlan,
    path: Path,
    shared_hashes: dict[str, str],
    global_hashes: dict[str, str],
) -> str:
    argv, cwd, _env, env_overrides = _resolved_execution(root, spec, plan, file_path=path)
    rel = path.relative_to(root).as_posix()
    return stable_fingerprint({
        "schema": CERTIFIED_FILE_SCHEMA,
        "engineFingerprint": engine_fingerprint(),
        "suite": spec.suite,
        "file": rel,
        "fileSha256": sha256_file(path),
        "argv": list(argv),
        "tool": tool_identity(spec.tool) if spec.tool else None,
        "sharedInputs": shared_hashes,
        "globalInputs": global_hashes,
        "execution": _execution_fingerprint(root, spec, cwd, env_overrides),
    })


def _metrics(workspace: RunWorkspace, *, cold: bool, store: CertifiedEvidenceStore) -> dict[str, Any]:
    payload = {
        "mode": "cold" if cold else "incremental",
        "coldRequested": cold,
        "evidencePath": str(store.path),
        "evidenceState": store.state,
        "executedEvidence": 0,
        "reusedEvidence": 0,
        "estimatedSavedSeconds": 0.0,
        "suites": {},
    }
    workspace.report["certification"] = payload
    workspace.persist()
    return payload


def _record(metrics: dict[str, Any], *, reused: bool, duration: float = 0.0) -> None:
    key = "reusedEvidence" if reused else "executedEvidence"
    metrics[key] = int(metrics.get(key) or 0) + 1
    if reused:
        metrics["estimatedSavedSeconds"] = round(
            float(metrics.get("estimatedSavedSeconds") or 0.0) + max(0.0, float(duration)),
            6,
        )


def _reuse_suite(
    *,
    root: Path,
    spec: SuiteCommand,
    store: CertifiedEvidenceStore,
    input_fingerprint: str,
    cold: bool,
) -> dict[str, Any] | None:
    if cold:
        return None
    entry = store.probe("suite", spec.suite, input_fingerprint)
    if entry is None:
        return None
    result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
    if not cached_outputs_match(root, spec.outputs, result):
        return None
    return entry


def _certify_file_set(
    *,
    root: Path,
    config: CheckConfig,
    plan: SelectionPlan,
    spec: SuiteCommand,
    process: ManagedProcessRunner,
    workspace: RunWorkspace,
    store: CertifiedEvidenceStore,
    metrics: dict[str, Any],
    files: list[Path],
    global_hashes: dict[str, str],
    reusable: bool,
    cold: bool,
) -> tuple[bool, dict[str, Any], list[str], float]:
    assert spec.file_set is not None
    shared_hashes = matching_input_hashes(root, spec.inputs, config.ignore)
    passed = 0
    failed: list[dict[str, Any]] = []
    file_results: dict[str, Any] = {}
    reused_files: list[str] = []
    executed_files: list[str] = []
    total_duration = 0.0
    timeout = spec.file_set.per_file_timeout_seconds or spec.timeout_seconds

    for index, path in enumerate(files, start=1):
        rel = path.relative_to(root).as_posix()
        input_fingerprint = _file_input_fingerprint(
            root=root,
            spec=spec,
            plan=plan,
            path=path,
            shared_hashes=shared_hashes,
            global_hashes=global_hashes,
        )
        entry = None
        if reusable and not cold:
            entry = store.probe("file-set-item", f"{spec.suite}:{rel}", input_fingerprint)
        if entry is not None:
            stored = entry.get("result") if isinstance(entry.get("result"), dict) else {}
            stats = dict(stored)
            stats.update({
                "status": "pass",
                "durationSeconds": 0.0,
                "certifiedEvidence": "reused",
                "certifiedAtUtc": entry.get("completedAtUtc"),
            })
            file_results[rel] = stats
            reused_files.append(rel)
            passed += 1
            _record(metrics, reused=True, duration=float(entry.get("durationSeconds") or 0.0))
            continue

        argv, cwd, env, _overrides = _resolved_execution(root, spec, plan, file_path=path)
        result = process.run(
            f"{spec.suite}.{index:04d}",
            argv,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout,
            idle_timeout_seconds=spec.idle_timeout_seconds,
            on_process_started=workspace.process_started,
        )
        total_duration += result.duration_seconds
        evaluation = evaluate_suite_result(
            result,
            root=root,
            kind=spec.adapter.kind,
            options=spec.adapter.options,
        )
        stats = {
            "status": "pass" if evaluation.accepted else ("timeout" if result.timed_out else "fail"),
            "exitCode": result.returncode,
            "durationSeconds": round(result.duration_seconds, 6),
            "timedOut": result.timed_out,
            "log": str(result.log_path),
            "result": evaluation.result,
            "certifiedEvidence": "executed" if reusable else "not-reusable",
        }
        file_results[rel] = stats
        executed_files.append(rel)
        if reusable:
            _record(metrics, reused=False)
        if evaluation.accepted:
            passed += 1
            if reusable:
                store.queue(
                    kind="file-set-item",
                    item_id=f"{spec.suite}:{rel}",
                    input_fingerprint=input_fingerprint,
                    source_fingerprint=_source_fingerprint(global_hashes, {**shared_hashes, rel: sha256_file(path)}),
                    completed_at_utc=utc_now(),
                    duration_seconds=result.duration_seconds,
                    result={
                        "exitCode": result.returncode,
                        "timedOut": result.timed_out,
                        "result": evaluation.result,
                    },
                )
        else:
            failed.append({"file": rel, "diagnostics": list(evaluation.diagnostics), **stats})
            break

    accepted = not failed
    payload = {
        "total": len(files),
        "passed": passed,
        "failed": failed,
        "fileResults": file_results,
        "execution": "file-set",
        "reusedFiles": reused_files,
        "executedFiles": executed_files,
    }
    diagnostics: list[str] = []
    if failed:
        first = failed[0]
        details = "; ".join(first.get("diagnostics") or []) or f"exit code {first.get('exitCode')}"
        diagnostics.append(f"{first.get('file')}: {details}")
    return accepted, payload, diagnostics, total_duration


def certify_plan(
    root: Path,
    config: CheckConfig,
    plan: SelectionPlan,
    *,
    cold: bool = False,
    resume: bool = False,
    max_chunks: int | None = None,
    time_slice_seconds: float | None = None,
) -> tuple[int, dict[str, Any]]:
    root = root.resolve()
    workspace = RunWorkspace(root, "check-certify")
    store = CertifiedEvidenceStore((root / config.certification.evidence_path).resolve())
    process = ManagedProcessRunner(root=root, log_dir=workspace.run_dir / "logs")
    budget: dict[str, Any] | None = None
    if max_chunks is not None or time_slice_seconds is not None:
        budget = {
            "remaining": None if max_chunks is None else max(0, int(max_chunks)),
            "deadline": None if time_slice_seconds is None else time.monotonic() + max(0.01, float(time_slice_seconds)),
        }
    workspace.report["selection"] = plan.to_dict()
    workspace.report["resumable"] = True
    workspace.report["resumeRequested"] = bool(resume)
    metrics = _metrics(workspace, cold=cold, store=store)
    global_hashes = matching_input_hashes(root, config.certification.global_inputs, config.ignore)
    reusable_suites = set(config.certification.reusable_suites)

    try:
        selected = [str(item.get("id")) for item in plan.selected]
        for suite in selected:
            spec = config.commands.get(suite)
            if spec is None:
                raise CheckRunError(suite, f"no command configured for selected suite {suite!r}")
            reusable = suite in reusable_suites
            workspace.stage_started(suite)
            missing = missing_required(root, spec.requires)
            if missing:
                raise CheckRunError(suite, "missing required path: " + ", ".join(missing))

            file_paths = _file_set_paths(root, spec.file_set, config.ignore) if spec.file_set is not None else []
            if spec.file_set is not None and not file_paths and not spec.file_set.allow_empty:
                raise CheckRunError(suite, "fileSet matched no files")

            input_hashes = matching_input_hashes(root, spec.inputs, config.ignore)
            for path in file_paths:
                input_hashes[path.relative_to(root).as_posix()] = sha256_file(path)
            input_hashes = dict(sorted(input_hashes.items()))

            if spec.file_set is None:
                input_fingerprint = _suite_input_fingerprint(
                    root=root,
                    spec=spec,
                    plan=plan,
                    input_hashes=input_hashes,
                    global_hashes=global_hashes,
                )
                entry = _reuse_suite(
                    root=root,
                    spec=spec,
                    store=store,
                    input_fingerprint=input_fingerprint,
                    cold=cold or not reusable,
                )
                if entry is not None:
                    result_payload = entry.get("result") if isinstance(entry.get("result"), dict) else {}
                    saved = float(entry.get("durationSeconds") or 0.0)
                    _record(metrics, reused=True, duration=saved)
                    metrics["suites"][suite] = {
                        "status": "reused",
                        "estimatedSavedSeconds": round(saved, 6),
                    }
                    workspace.stage_finished(suite, {
                        "status": "pass",
                        "durationSeconds": 0.0,
                        "resultAdapter": spec.adapter.kind,
                        "result": result_payload,
                        "certifiedEvidence": {
                            "status": "reused",
                            "inputFingerprint": input_fingerprint,
                            "certifiedAtUtc": entry.get("completedAtUtc"),
                        },
                    })
                    continue

                argv, cwd, env, _env_overrides = _resolved_execution(root, spec, plan)
                if spec.chunks:
                    suite_resume_key = _resume_key(suite=suite, cache_key=input_fingerprint, spec=spec)
                    workspace.report["checks"].setdefault(suite, {})["resumeKey"] = suite_resume_key
                    workspace.persist()
                    reusable_chunks: dict[str, dict[str, Any]] = {}
                    resume_source: str | None = None
                    if resume:
                        reusable_chunks, resume_source = _compatible_resume_chunks(
                            root,
                            mode="check-certify",
                            selection=plan.to_dict(),
                            suite=suite,
                            resume_key=suite_resume_key,
                            current_run_dir=workspace.run_dir,
                        )
                    accepted, evaluation_result, diagnostics, duration, timed_out, partial = _execute_chunks(
                        root=root,
                        spec=spec,
                        plan=plan,
                        process=process,
                        workspace=workspace,
                        reusable=reusable_chunks,
                        resume_source=resume_source,
                        budget=budget,
                    )
                    if partial:
                        workspace.stage_finished(suite, {
                            "status": "partial",
                            "durationSeconds": round(duration, 3),
                            "resumeKey": suite_resume_key,
                            "resultAdapter": spec.adapter.kind,
                            "result": evaluation_result,
                            "certifiedEvidence": {"status": "pending" if reusable else "not-reusable"},
                        })
                        workspace.finalize("partial", message="certification slice completed; resume to continue remaining chunks")
                        return 0, workspace.report
                    outputs_ok, outputs_payload, output_diagnostics = evaluate_outputs(root, spec.outputs)
                    evaluation_result = dict(evaluation_result)
                    if spec.outputs.required or spec.outputs.capture:
                        evaluation_result["outputs"] = outputs_payload
                    if not outputs_ok:
                        accepted = False
                        diagnostics.extend(output_diagnostics)
                    if reusable:
                        _record(metrics, reused=False)
                    payload = {
                        "status": "pass" if accepted else ("timeout" if timed_out else "fail"),
                        "exitCode": 0 if accepted else 1,
                        "durationSeconds": round(duration, 3),
                        "timedOut": timed_out,
                        "inputFiles": len(input_hashes),
                        "resultAdapter": spec.adapter.kind,
                        "result": evaluation_result,
                        "certifiedEvidence": {
                            "status": "executed" if reusable else "not-reusable",
                            "inputFingerprint": input_fingerprint if reusable else None,
                        },
                    }
                    if not accepted:
                        payload["diagnostics"] = list(dict.fromkeys(diagnostics))
                    workspace.stage_finished(suite, payload)
                    metrics["suites"][suite] = {
                        "status": "executed",
                        "durationSeconds": round(duration, 6),
                        "reusable": reusable,
                        "chunks": len(spec.chunks),
                    }
                    if not accepted:
                        message = "; ".join(dict.fromkeys(diagnostics)) or "chunked certification suite failed"
                        raise CheckRunError(suite, message)
                    if reusable:
                        store.queue(
                            kind="suite",
                            item_id=suite,
                            input_fingerprint=input_fingerprint,
                            source_fingerprint=_source_fingerprint(global_hashes, input_hashes),
                            completed_at_utc=utc_now(),
                            duration_seconds=duration,
                            result={"inputFiles": len(input_hashes), **evaluation_result},
                        )
                    continue

                result = process.run(
                    suite,
                    argv,
                    cwd=cwd,
                    env=env,
                    timeout_seconds=spec.timeout_seconds,
                    idle_timeout_seconds=spec.idle_timeout_seconds,
                    on_process_started=workspace.process_started,
                )
                evaluation = evaluate_suite_result(
                    result,
                    root=root,
                    kind=spec.adapter.kind,
                    options=spec.adapter.options,
                )
                accepted = evaluation.accepted
                evaluation_result = dict(evaluation.result)
                diagnostics = list(evaluation.diagnostics)
                outputs_ok, outputs_payload, output_diagnostics = evaluate_outputs(root, spec.outputs)
                if spec.outputs.required or spec.outputs.capture:
                    evaluation_result["outputs"] = outputs_payload
                if not outputs_ok:
                    accepted = False
                    diagnostics.extend(output_diagnostics)
                if reusable:
                    _record(metrics, reused=False)
                payload: dict[str, Any] = {
                    "status": "pass" if accepted else ("timeout" if result.timed_out else "fail"),
                    "exitCode": result.returncode,
                    "durationSeconds": round(result.duration_seconds, 3),
                    "timedOut": result.timed_out,
                    "log": str(result.log_path),
                    "inputFiles": len(input_hashes),
                    "resultAdapter": spec.adapter.kind,
                    "result": evaluation_result,
                    "certifiedEvidence": {
                        "status": "executed" if reusable else "not-reusable",
                        "inputFingerprint": input_fingerprint if reusable else None,
                    },
                }
                if not accepted:
                    payload["diagnostics"] = list(dict.fromkeys(diagnostics))
                workspace.stage_finished(suite, payload)
                metrics["suites"][suite] = {
                    "status": "executed",
                    "durationSeconds": round(result.duration_seconds, 6),
                    "reusable": reusable,
                }
                if not accepted:
                    message = "; ".join(dict.fromkeys(diagnostics)) or f"suite result rejected (exit code {result.returncode})"
                    raise CheckRunError(suite, message)
                if reusable:
                    store.queue(
                        kind="suite",
                        item_id=suite,
                        input_fingerprint=input_fingerprint,
                        source_fingerprint=_source_fingerprint(global_hashes, input_hashes),
                        completed_at_utc=utc_now(),
                        duration_seconds=result.duration_seconds,
                        result={"inputFiles": len(input_hashes), **evaluation_result},
                    )
                continue

            accepted, evaluation_result, diagnostics, duration = _certify_file_set(
                root=root,
                config=config,
                plan=plan,
                spec=spec,
                process=process,
                workspace=workspace,
                store=store,
                metrics=metrics,
                files=file_paths,
                global_hashes=global_hashes,
                reusable=reusable,
                cold=cold,
            )
            outputs_ok, outputs_payload, output_diagnostics = evaluate_outputs(root, spec.outputs)
            evaluation_result = dict(evaluation_result)
            if spec.outputs.required or spec.outputs.capture:
                evaluation_result["outputs"] = outputs_payload
            if not outputs_ok:
                accepted = False
                diagnostics.extend(output_diagnostics)
            timed_out = any(bool(item.get("timedOut")) for item in evaluation_result.get("failed", []))
            payload = {
                "status": "pass" if accepted else ("timeout" if timed_out else "fail"),
                "exitCode": 0 if accepted else 1,
                "durationSeconds": round(duration, 3),
                "timedOut": timed_out,
                "inputFiles": len(input_hashes),
                "resultAdapter": spec.adapter.kind,
                "result": evaluation_result,
                "certifiedEvidence": {
                    "status": "granular" if reusable else "not-reusable",
                    "reused": len(evaluation_result.get("reusedFiles") or []),
                    "executed": len(evaluation_result.get("executedFiles") or []),
                },
            }
            if not accepted:
                payload["diagnostics"] = list(dict.fromkeys(diagnostics))
            workspace.stage_finished(suite, payload)
            metrics["suites"][suite] = {
                "status": "granular",
                "reused": len(evaluation_result.get("reusedFiles") or []),
                "executed": len(evaluation_result.get("executedFiles") or []),
                "durationSeconds": round(duration, 6),
                "reusable": reusable,
            }
            if not accepted:
                message = "; ".join(dict.fromkeys(diagnostics)) or "file-set certification failed"
                raise CheckRunError(suite, message)

        # Critical invariant: pending PASS facts are promoted only after the entire certify run passed.
        store.flush()
        metrics["evidenceStateAfter"] = store.state
        workspace.finalize("pass")
        return 0, workspace.report
    except (CheckRunError, WorkspaceError) as exc:
        # Intentionally do not flush queued evidence on failure/interruption.
        workspace.finalize("fail", message=str(exc))
        return 1, workspace.report
