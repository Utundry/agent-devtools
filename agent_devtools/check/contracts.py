from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Iterable

from agent_devtools.core.hashing import sha256_file

from .config import OutputSpec


def _has_magic(pattern: str) -> bool:
    return any(char in pattern for char in "*?[")


def _iter_project_paths(root: Path):
    for path in root.rglob("*"):
        yield path, path.relative_to(root).as_posix()


def matching_paths(root: Path, patterns: Iterable[str], *, files_only: bool = True) -> list[Path]:
    root = root.resolve()
    found: dict[str, Path] = {}
    for pattern in patterns:
        pattern = str(pattern).replace("\\", "/")
        if not pattern:
            continue
        if not _has_magic(pattern):
            candidate = (root / pattern).resolve()
            try:
                rel = candidate.relative_to(root).as_posix()
            except ValueError:
                continue
            if candidate.exists() and (candidate.is_file() or not files_only):
                found[rel] = candidate
            continue
        for path, rel in _iter_project_paths(root):
            if files_only and not path.is_file():
                continue
            if fnmatch.fnmatchcase(rel, pattern):
                found[rel] = path
    return [found[key] for key in sorted(found)]


def missing_required(root: Path, patterns: Iterable[str]) -> list[str]:
    missing: list[str] = []
    for pattern in patterns:
        if not matching_paths(root, (pattern,), files_only=False):
            missing.append(str(pattern))
    return missing


def capture_hashes(root: Path, patterns: Iterable[str]) -> dict[str, str]:
    root = root.resolve()
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in matching_paths(root, patterns, files_only=True)
    }


def evaluate_outputs(root: Path, spec: OutputSpec) -> tuple[bool, dict[str, object], list[str]]:
    missing = missing_required(root, spec.required)
    hashes = capture_hashes(root, spec.capture) if spec.capture else {}
    payload: dict[str, object] = {
        "required": list(spec.required),
        "missing": missing,
        "capture": list(spec.capture),
        "capturedFiles": len(hashes),
        "hashes": hashes,
    }
    diagnostics = ["missing required output: " + ", ".join(missing)] if missing else []
    return not missing, payload, diagnostics


def cached_outputs_match(root: Path, spec: OutputSpec, cached_result: dict[str, object]) -> bool:
    if spec.required and missing_required(root, spec.required):
        return False
    if not spec.capture:
        return True
    outputs = cached_result.get("outputs")
    if not isinstance(outputs, dict):
        return False
    expected = outputs.get("hashes")
    if not isinstance(expected, dict):
        return False
    normalized = {str(key): str(value) for key, value in expected.items()}
    return capture_hashes(root, spec.capture) == normalized
