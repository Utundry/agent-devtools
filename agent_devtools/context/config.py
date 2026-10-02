from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_devtools.core.hashing import stable_fingerprint


class ContextConfigError(RuntimeError):
    pass


_DEFAULT_INCLUDE = (
    "*.py", "**/*.py", "*.md", "**/*.md", "*.json", "**/*.json",
    "*.toml", "**/*.toml", "*.ini", "**/*.ini", "*.cfg", "**/*.cfg",
    "*.yaml", "**/*.yaml", "*.yml", "**/*.yml", "*.sql", "**/*.sql",
    "*.php", "**/*.php", "*.js", "**/*.js", "*.ts", "**/*.ts",
    "*.tsx", "**/*.tsx", "*.vue", "**/*.vue", "*.html", "**/*.html",
    "*.css", "**/*.css", "*.sh", "**/*.sh", "*.txt", "**/*.txt",
)
_DEFAULT_IGNORE = (
    ".git/**", ".agent-cache/**", ".agent-work/**", "**/__pycache__/**",
    "**/node_modules/**", "**/vendor/**", "**/.venv/**", "**/venv/**", "dist/**", "build/**", "coverage/**",
    "*.min.js", "*.min.css", "*.map", "*.pyc", "*.pyo", "*.log", "*.tmp",
)
_DEFAULT_KIND_CAPS = {
    "source": 0.60,
    "test": 0.30,
    "documentation": 0.30,
    "architecture": 0.30,
    "schema": 0.20,
    "history": 0.15,
    "release": 0.15,
    "knowledge": 0.35,
}


@dataclass(frozen=True)
class SourceKindRule:
    glob: str
    kind: str
    weight: float


@dataclass(frozen=True)
class ContextConfig:
    root: Path
    database: Path
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    low_priority: tuple[str, ...]
    max_file_bytes: int
    chunk_lines: int
    overlap_lines: int
    source_kinds: tuple[SourceKindRule, ...]
    semantic_map: Path | None
    candidate_files: int
    candidate_chunks: int
    default_budget: int
    max_graph_depth: int
    kind_caps: dict[str, float]
    low_priority_factor: float
    fingerprint: str


def _string_list(value: Any, field: str, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ContextConfigError(f"{field} must be an array of non-empty strings")
    return tuple(item.strip() for item in value)


def _safe_relative(root: Path, raw: str, field: str) -> Path:
    rel = Path(raw)
    if rel.is_absolute() or ".." in rel.parts:
        raise ContextConfigError(f"{field} must stay inside the project root")
    resolved = (root / rel).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ContextConfigError(f"{field} must stay inside the project root") from exc
    return resolved


def _positive_int(value: Any, default: int, field: str, low: int, high: int) -> int:
    try:
        result = int(value if value is not None else default)
    except (TypeError, ValueError) as exc:
        raise ContextConfigError(f"{field} must be an integer") from exc
    if result < low or result > high:
        raise ContextConfigError(f"{field} must be between {low} and {high}")
    return result


def load_context_config(root: Path, config_path: Path | None = None) -> ContextConfig:
    root = root.resolve()
    path = (config_path if config_path and config_path.is_absolute() else root / (config_path or "agent-tools.json")).resolve()
    raw: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContextConfigError(f"Agent DevTools config is unreadable: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ContextConfigError("Agent DevTools config root must be an object")
        raw = loaded

    context = raw.get("context", {})
    if context is None:
        context = {}
    if not isinstance(context, dict):
        raise ContextConfigError("context must be an object")
    # Project ignores extend context safety defaults instead of replacing them.
    ignore = tuple(dict.fromkeys((*_DEFAULT_IGNORE, *_string_list(raw.get("ignore"), "ignore", ()))))
    include_runtime = context.get("includeRuntime", False)
    if not isinstance(include_runtime, bool):
        raise ContextConfigError("context.includeRuntime must be a boolean")
    if not include_runtime:
        ignore = (*ignore, "devtools/agent/**")
        runtime_root = Path(__file__).resolve().parents[2]
        try:
            runtime_rel = runtime_root.relative_to(root)
        except ValueError:
            pass
        else:
            # Own source remains indexable when it IS the project root.
            if runtime_rel.parts:
                ignore = (*ignore, runtime_rel.as_posix() + "/**")
    include = _string_list(context.get("include"), "context.include", _DEFAULT_INCLUDE)
    extra_exclude = _string_list(context.get("exclude"), "context.exclude", ())
    low_priority = _string_list(context.get("lowPriority"), "context.lowPriority", ())
    database = _safe_relative(root, str(context.get("database") or ".agent-cache/context.sqlite"), "context.database")
    max_file_bytes = _positive_int(context.get("maxFileBytes"), 1_048_576, "context.maxFileBytes", 1_024, 64 * 1024 * 1024)
    chunk_lines = _positive_int(context.get("chunkLines"), 80, "context.chunkLines", 20, 500)
    overlap_lines = _positive_int(context.get("overlapLines"), 10, "context.overlapLines", 0, chunk_lines - 1)
    candidate_files = _positive_int(context.get("candidateFiles"), 20, "context.candidateFiles", 1, 500)
    candidate_chunks = _positive_int(context.get("candidateChunks"), 80, "context.candidateChunks", 8, 2000)
    default_budget = _positive_int(context.get("defaultBudget"), 3500, "context.defaultBudget", 128, 100_000)
    max_graph_depth = _positive_int(context.get("maxGraphDepth"), 1, "context.maxGraphDepth", 0, 3)
    try:
        low_priority_factor = float(context.get("lowPriorityFactor", 0.20))
    except (TypeError, ValueError) as exc:
        raise ContextConfigError("context.lowPriorityFactor must be numeric") from exc
    if not 0 < low_priority_factor <= 1:
        raise ContextConfigError("context.lowPriorityFactor must be > 0 and <= 1")

    semantic_map_raw = context.get("semanticMap", "agent-context.map.json")
    semantic_map: Path | None
    if semantic_map_raw in (None, False, ""):
        semantic_map = None
    elif not isinstance(semantic_map_raw, str):
        raise ContextConfigError("context.semanticMap must be a project-relative path or null")
    else:
        semantic_map = _safe_relative(root, semantic_map_raw, "context.semanticMap")

    raw_caps = context.get("kindCaps", _DEFAULT_KIND_CAPS)
    if not isinstance(raw_caps, dict):
        raise ContextConfigError("context.kindCaps must be an object")
    kind_caps: dict[str, float] = {}
    for key, value in raw_caps.items():
        if not isinstance(key, str) or not key.strip():
            raise ContextConfigError("context.kindCaps keys must be non-empty strings")
        try:
            cap = float(value)
        except (TypeError, ValueError) as exc:
            raise ContextConfigError(f"context.kindCaps.{key} must be numeric") from exc
        if not 0 < cap <= 1:
            raise ContextConfigError(f"context.kindCaps.{key} must be > 0 and <= 1")
        kind_caps[key.strip()] = cap

    source_rules: list[SourceKindRule] = []
    source_kinds = raw.get("sourceKinds", [])
    if source_kinds is None:
        source_kinds = []
    if not isinstance(source_kinds, list):
        raise ContextConfigError("sourceKinds must be an array")
    for index, item in enumerate(source_kinds):
        if not isinstance(item, dict):
            raise ContextConfigError(f"sourceKinds[{index}] must be an object")
        glob = str(item.get("glob") or "").strip()
        kind = str(item.get("kind") or "").strip()
        try:
            weight = float(item.get("weight", 1.0))
        except (TypeError, ValueError) as exc:
            raise ContextConfigError(f"sourceKinds[{index}].weight must be numeric") from exc
        if not glob or not kind or weight <= 0:
            raise ContextConfigError(f"sourceKinds[{index}] requires glob, kind and positive weight")
        source_rules.append(SourceKindRule(glob, kind, weight))

    identity = {
        "schema": "agent-devtools-context-config-v2",
        "include": include,
        "exclude": (*ignore, *extra_exclude),
        "lowPriority": low_priority,
        "maxFileBytes": max_file_bytes,
        "chunkLines": chunk_lines,
        "overlapLines": overlap_lines,
        "sourceKinds": [rule.__dict__ for rule in source_rules],
        "semanticMap": (semantic_map.relative_to(root).as_posix() if semantic_map else None),
        "candidateFiles": candidate_files,
        "candidateChunks": candidate_chunks,
        "defaultBudget": default_budget,
        "maxGraphDepth": max_graph_depth,
        "kindCaps": kind_caps,
        "lowPriorityFactor": low_priority_factor,
    }
    return ContextConfig(
        root=root,
        database=database,
        include=include,
        exclude=(*ignore, *extra_exclude),
        low_priority=low_priority,
        max_file_bytes=max_file_bytes,
        chunk_lines=chunk_lines,
        overlap_lines=overlap_lines,
        source_kinds=tuple(source_rules),
        semantic_map=semantic_map,
        candidate_files=candidate_files,
        candidate_chunks=candidate_chunks,
        default_budget=default_budget,
        max_graph_depth=max_graph_depth,
        kind_caps=dict(sorted(kind_caps.items())),
        low_priority_factor=low_priority_factor,
        fingerprint=stable_fingerprint(identity),
    )
