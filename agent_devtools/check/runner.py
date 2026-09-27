from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from agent_devtools.core.hashing import sha256_file
from agent_devtools.core.process import ManagedProcessRunner
from agent_devtools.core.workspace import RunWorkspace, WorkspaceError, default_work_root, utc_now

from .adapters import evaluate_suite_result
from .cache import StageCache, matching_input_hashes
from .config import CheckConfig, FileSetSpec, SuiteCommand
from .contracts import cached_outputs_match, evaluate_outputs, matching_paths, missing_required
from .policy import SelectionPlan


class CheckRunError(RuntimeError):
    def __init__(self, suite: str, message: str) -> None:
        super().__init__(message)
        self.suite = suite


def _expand(value: str, root: Path, plan: SelectionPlan, *, file_path: Path | None = None) -> str:
    application_groups = ",".join(str(item.get("id")) for item in plan.selected_application_groups)
    expanded = (
        value
        .replace("{python}", sys.executable)
        .replace("{root}", str(root))
        .replace("{selected_application_groups_csv}", application_groups)
    )
    if file_path is not None:
        rel = file_path.resolve().relative_to(root.resolve()).as_posix()
        expanded = expanded.replace("{file_abs}", str(file_path.resolve())).replace("{file}", rel)
    return expanded


def _resolved_execution(
    root: Path,
    spec: SuiteCommand,
    plan: SelectionPlan,
    *,
    file_path: Path | None = None,
) -> tuple[tuple[str, ...], Path, dict[str, str], dict[str, str]]:
    argv = tuple(_expand(item, root, plan, file_path=file_path) for item in spec.argv)
    cwd = root if spec.cwd is None else (root / _expand(spec.cwd, root, plan, file_path=file_path)).resolve()
    try:
        cwd.relative_to(root)
    except ValueError as exc:
        raise CheckRunError(spec.suite, f"suite cwd escapes project root: {spec.cwd}") from exc
    if not cwd.is_dir():
        raise CheckRunError(spec.suite, f"suite cwd does not exist: {cwd}")
    env = dict(os.environ)
    overrides: dict[str, str] = {}
    for key, value in spec.env.items():
        overrides[key] = _expand(value, root, plan, file_path=file_path)
        env[key] = overrides[key]
    return argv, cwd, env, overrides


def _execution_fingerprint(root: Path, spec: SuiteCommand, cwd: Path, env_overrides: dict[str, str]) -> dict[str, Any]:
    return {
        "cwd": cwd.relative_to(root).as_posix() if cwd != root else ".",
        "env": dict(sorted(env_overrides.items())),
        "adapter": spec.adapter.to_dict(),
        "requires": list(spec.requires),
        "fileSet": spec.file_set.to_dict() if spec.file_set is not None else None,
        "outputs": spec.outputs.to_dict(),
        "idleTimeoutSeconds": spec.idle_timeout_seconds,
        "chunks": [chunk.to_dict() for chunk in spec.chunks],
    }


def _file_set_paths(root: Path, file_set: FileSetSpec, ignore: tuple[str, ...]) -> list[Path]:
    excluded = (*ignore, *file_set.exclude)
    result: list[Path] = []
    for path in matching_paths(root, file_set.patterns):
        rel = path.relative_to(root).as_posix()
        if any(fnmatch.fnmatchcase(rel, pattern) for pattern in excluded):
            continue
        result.append(path)
    return result


def _input_hashes(root: Path, spec: SuiteCommand, config: CheckConfig, file_paths: list[Path]) -> dict[str, str]:
    hashes = matching_input_hashes(root, spec.inputs, config.ignore)
    for path in file_paths:
        rel = path.relative_to(root).as_posix()
        hashes[rel] = sha256_file(path)
    return dict(sorted(hashes.items()))


def _execute_file_set(
    *,
    root: Path,
    spec: SuiteCommand,
    plan: SelectionPlan,
    process: ManagedProcessRunner,
    workspace: RunWorkspace,
    files: list[Path],
) -> tuple[bool, dict[str, Any], list[str], float]:
    passed = 0
    failed: list[dict[str, Any]] = []
    file_results: dict[str, Any] = {}
    total_duration = 0.0
    assert spec.file_set is not None
    timeout = spec.file_set.per_file_timeout_seconds or spec.timeout_seconds
    for index, path in enumerate(files, start=1):
        rel = path.relative_to(root).as_posix()
        argv, cwd, env, _overrides = _resolved_execution(root, spec, plan, file_path=path)
        result = process.run(
            f"{spec.suite}.{index:04d}",
            argv,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout,
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
        }
        file_results[rel] = stats
        if evaluation.accepted:
            passed += 1
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
    }
    diagnostics: list[str] = []
    if failed:
        first = failed[0]
        details = "; ".join(first.get("diagnostics") or []) or f"exit code {first.get('exitCode')}"
        diagnostics.append(f"{first.get('file')}: {details}")
    return accepted, payload, diagnostics, total_duration




def _resume_key(*, suite: str, cache_key: str, spec: SuiteCommand) -> str:
    payload = {
        "suite": suite,
        "cacheKey": cache_key,
        "chunks": [chunk.to_dict() for chunk in spec.chunks],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _compatible_resume_chunks(
    root: Path,
    *,
    mode: str,
    selection: dict[str, Any],
    suite: str,
    resume_key: str,
    current_run_dir: Path,
) -> tuple[dict[str, dict[str, Any]], str | None]:
    runs_root = default_work_root(root) / "runs"
    if not runs_root.is_dir():
        return {}, None
    candidates = sorted(
        (path for path in runs_root.iterdir() if path.is_dir() and path != current_run_dir),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for run_dir in candidates:
        report_path = run_dir / "report.json"
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(report, dict) or report.get("mode") != mode or report.get("selection") != selection:
            continue
        suite_payload = ((report.get("checks") or {}).get(suite) or {})
        if not isinstance(suite_payload, dict) or suite_payload.get("resumeKey") != resume_key:
            continue
        chunks = suite_payload.get("chunks")
        if not isinstance(chunks, dict):
            continue
        reusable = {
            str(chunk_id): dict(payload)
            for chunk_id, payload in chunks.items()
            if isinstance(payload, dict) and payload.get("status") == "pass"
        }
        if reusable:
            return reusable, str(run_dir)
    return {}, None


def _execute_chunks(
    *,
    root: Path,
    spec: SuiteCommand,
    plan: SelectionPlan,
    process: ManagedProcessRunner,
    workspace: RunWorkspace,
    reusable: dict[str, dict[str, Any]],
    resume_source: str | None,
    budget: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any], list[str], float, bool, bool]:
    chunk_results: dict[str, Any] = {}
    total_duration = 0.0
    any_timeout = False
    for index, chunk in enumerate(spec.chunks, start=1):
        if chunk.id in reusable:
            prior = dict(reusable[chunk.id])
            prior["resumed"] = True
            if resume_source:
                prior["resumeSource"] = resume_source
            chunk_results[chunk.id] = prior
            workspace.chunk_finished(spec.suite, chunk.id, prior)
            continue
        if budget is not None:
            remaining = budget.get("remaining")
            deadline = budget.get("deadline")
            if (remaining is not None and int(remaining) <= 0) or (deadline is not None and time.monotonic() >= float(deadline)):
                return True, {"execution": "chunks", "chunks": chunk_results}, [], total_duration, any_timeout, True
            if remaining is not None:
                budget["remaining"] = int(remaining) - 1
        argv = tuple(_expand(item, root, plan) for item in chunk.argv)
        _base_argv, cwd, env, _overrides = _resolved_execution(root, spec, plan)
        workspace.chunk_started(spec.suite, chunk.id, index=index, total=len(spec.chunks))
        result = process.run(
            f"{spec.suite}.{chunk.id}",
            argv,
            cwd=cwd,
            env=env,
            timeout_seconds=chunk.timeout_seconds or spec.timeout_seconds,
            idle_timeout_seconds=chunk.idle_timeout_seconds or spec.idle_timeout_seconds,
            on_process_started=workspace.process_started,
        )
        total_duration += result.duration_seconds
        evaluation = evaluate_suite_result(
            result,
            root=root,
            kind=spec.adapter.kind,
            options=spec.adapter.options,
        )
        payload = {
            "status": "pass" if evaluation.accepted else ("timeout" if result.timed_out else "fail"),
            "exitCode": result.returncode,
            "durationSeconds": round(result.duration_seconds, 6),
            "timedOut": result.timed_out,
            "timeoutReason": result.timeout_reason,
            "log": str(result.log_path),
            "result": evaluation.result,
            "diagnostics": list(evaluation.diagnostics),
            "resumed": False,
        }
        chunk_results[chunk.id] = payload
        workspace.chunk_finished(spec.suite, chunk.id, payload)
        if not evaluation.accepted:
            any_timeout = result.timed_out
            diagnostics = list(evaluation.diagnostics)
            if result.timed_out and not diagnostics:
                diagnostics = [f"chunk {chunk.id} timed out ({result.timeout_reason or 'hard'})"]
            return False, {"execution": "chunks", "chunks": chunk_results}, diagnostics, total_duration, any_timeout, False
    return True, {"execution": "chunks", "chunks": chunk_results}, [], total_duration, any_timeout, False

def run_plan(
    root: Path,
    config: CheckConfig,
    plan: SelectionPlan,
    *,
    cache_enabled: bool = True,
    resume: bool = False,
    max_chunks: int | None = None,
    time_slice_seconds: float | None = None,
) -> tuple[int, dict[str, Any]]:
    root = root.resolve()
    workspace = RunWorkspace(root, f"check-{plan.profile}")
    cache = StageCache(root / ".agent-cache" / "check-stage-cache-v1.json")
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
    workspace.persist()
    try:
        selected = [str(item.get("id")) for item in plan.selected]
        for suite in selected:
            spec = config.commands.get(suite)
            if spec is None:
                raise CheckRunError(suite, f"no command configured for selected suite {suite!r}")

            workspace.stage_started(suite)
            missing = missing_required(root, spec.requires)
            if missing:
                raise CheckRunError(suite, "missing required path: " + ", ".join(missing))

            file_paths = _file_set_paths(root, spec.file_set, config.ignore) if spec.file_set is not None else []
            if spec.file_set is not None and not file_paths and not spec.file_set.allow_empty:
                raise CheckRunError(suite, "fileSet matched no files")

            argv, cwd, env, env_overrides = _resolved_execution(root, spec, plan)
            input_hashes = _input_hashes(root, spec, config, file_paths)
            execution_fingerprint = _execution_fingerprint(root, spec, cwd, env_overrides)
            cache_key = cache.key(
                suite=suite,
                argv=argv,
                tool=spec.tool,
                input_hashes=input_hashes,
                execution=execution_fingerprint,
            )
            suite_resume_key = _resume_key(suite=suite, cache_key=cache_key, spec=spec)
            workspace.report["checks"].setdefault(suite, {})["resumeKey"] = suite_resume_key
            workspace.persist()
            cached = cache.probe(cache_key, suite) if cache_enabled and spec.cache else None
            if cached is not None:
                cached_result = cached.get("result") if isinstance(cached.get("result"), dict) else {}
                if cached_outputs_match(root, spec.outputs, cached_result):
                    workspace.stage_finished(suite, {
                        "status": "pass",
                        "durationSeconds": 0.0,
                        "cache": {"status": "hit", "key": cache_key, "cachedAtUtc": cached.get("completedAtUtc")},
                        "resultAdapter": spec.adapter.kind,
                        "result": cached_result,
                    })
                    continue

            if spec.file_set is not None:
                accepted, evaluation_result, diagnostics, duration = _execute_file_set(
                    root=root,
                    spec=spec,
                    plan=plan,
                    process=process,
                    workspace=workspace,
                    files=file_paths,
                )
                exit_code = 0 if accepted else 1
                timed_out = any(bool(item.get("timedOut")) for item in evaluation_result.get("failed", []))
                log_path = None
            elif spec.chunks:
                reusable: dict[str, dict[str, Any]] = {}
                resume_source: str | None = None
                if resume:
                    reusable, resume_source = _compatible_resume_chunks(
                        root,
                        mode=f"check-{plan.profile}",
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
                    reusable=reusable,
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
                    })
                    workspace.finalize("partial", message="execution slice completed; resume to continue remaining chunks")
                    return 0, workspace.report
                exit_code = 0 if accepted else 1
                log_path = None
            else:
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
                evaluation_result = evaluation.result
                diagnostics = list(evaluation.diagnostics)
                duration = result.duration_seconds
                exit_code = result.returncode
                timed_out = result.timed_out
                log_path = str(result.log_path)

            outputs_ok, outputs_payload, output_diagnostics = evaluate_outputs(root, spec.outputs)
            evaluation_result = dict(evaluation_result)
            if spec.outputs.required or spec.outputs.capture:
                evaluation_result["outputs"] = outputs_payload
            if not outputs_ok:
                accepted = False
                diagnostics.extend(output_diagnostics)

            payload: dict[str, Any] = {
                "status": "pass" if accepted else ("timeout" if timed_out else "fail"),
                "exitCode": exit_code,
                "durationSeconds": round(duration, 3),
                "timedOut": timed_out,
                "cache": {"status": "miss" if cache_enabled and spec.cache else "bypass", "key": cache_key if spec.cache else None},
                "inputFiles": len(input_hashes),
                "resultAdapter": spec.adapter.kind,
                "result": evaluation_result,
            }
            if log_path is not None:
                payload["log"] = log_path
            if not accepted:
                payload["diagnostics"] = list(dict.fromkeys(diagnostics))
            workspace.stage_finished(suite, payload)
            if not accepted:
                message = "; ".join(dict.fromkeys(diagnostics)) or f"suite result rejected (exit code {exit_code})"
                raise CheckRunError(suite, message)

            if cache_enabled and spec.cache:
                cache.queue(
                    cache_key,
                    suite=suite,
                    completed_at_utc=utc_now(),
                    result={"inputFiles": len(input_hashes), **evaluation_result},
                )

        cache.flush()
        workspace.finalize("pass")
        return 0, workspace.report
    except (CheckRunError, WorkspaceError) as exc:
        workspace.finalize("fail", message=str(exc))
        return 1, workspace.report
