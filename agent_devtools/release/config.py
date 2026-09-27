from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ReleaseConfigError(RuntimeError):
    pass


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class PackageSpec:
    package_id: str
    kind: str
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    required: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReleaseConfig:
    root: Path
    artifact_prefix: str
    packages: tuple[PackageSpec, ...]


def _strings(value: Any, field: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ReleaseConfigError(f"{field} must be {'a non-empty' if not allow_empty else 'an'} array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ReleaseConfigError(f"{field} must contain non-empty strings")
        result.append(item.strip())
    if len(result) != len(set(result)):
        raise ReleaseConfigError(f"{field} contains duplicates")
    return tuple(result)


def _validate_project_relative(pattern: str, field: str) -> None:
    p = Path(pattern)
    if p.is_absolute() or ".." in p.parts:
        raise ReleaseConfigError(f"{field} must stay inside the project root: {pattern!r}")


def load_release_config(root: Path, config_path: Path | None = None) -> ReleaseConfig:
    root = root.resolve()
    if config_path is None:
        path = root / "agent-tools.json"
    else:
        path = config_path if config_path.is_absolute() else root / config_path
    path = path.resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReleaseConfigError(f"Agent DevTools config not found: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseConfigError(f"Agent DevTools config is unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise ReleaseConfigError("Agent DevTools config root must be an object")
    release = raw.get("release")
    if not isinstance(release, dict):
        raise ReleaseConfigError("release must be an object")
    artifact_prefix = str(release.get("artifactPrefix") or root.name).strip()
    if not _SAFE_ID.fullmatch(artifact_prefix):
        raise ReleaseConfigError(
            "release.artifactPrefix (or the project directory name when omitted) must use only "
            "letters, digits, dot, underscore and dash"
        )
    raw_packages = release.get("packages")
    if not isinstance(raw_packages, list) or not raw_packages:
        raise ReleaseConfigError("release.packages must be a non-empty array")
    packages: list[PackageSpec] = []
    seen: set[str] = set()
    source_count = 0
    for index, item in enumerate(raw_packages):
        field = f"release.packages[{index}]"
        if not isinstance(item, dict):
            raise ReleaseConfigError(f"{field} must be an object")
        package_id = str(item.get("id") or "").strip()
        if not _SAFE_ID.fullmatch(package_id):
            raise ReleaseConfigError(f"{field}.id is invalid")
        if package_id in seen:
            raise ReleaseConfigError(f"duplicate release package id: {package_id}")
        seen.add(package_id)
        kind = str(item.get("kind") or "files").strip()
        if kind not in {"source", "files"}:
            raise ReleaseConfigError(f"{field}.kind must be 'source' or 'files'")
        if kind == "source":
            source_count += 1
        include = _strings(item.get("include", ["**"] if kind == "files" else []), f"{field}.include")
        exclude = _strings(item.get("exclude", []), f"{field}.exclude")
        required = _strings(item.get("required", []), f"{field}.required")
        for pattern in (*include, *exclude, *required):
            _validate_project_relative(pattern, field)
        packages.append(PackageSpec(package_id, kind, include, exclude, required))
    if source_count > 1:
        raise ReleaseConfigError("release.packages may declare at most one kind='source' package")
    return ReleaseConfig(root=root, artifact_prefix=artifact_prefix, packages=tuple(packages))
