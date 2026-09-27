from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_devtools.core.io import atomic_json_write

PRESET_FORMAT = "agent-devtools-preset"
COMPONENT_FORMAT = "agent-devtools-preset-component"
PRESET_VERSION = 1
_COMPONENT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class PresetError(RuntimeError):
    pass


@dataclass(frozen=True)
class Preset:
    preset_id: str
    title: str
    description: str
    requirements: tuple[str, ...]
    files: dict[str, Any]
    path: Path
    components: tuple[str, ...] = ()


def preset_root() -> Path:
    return Path(__file__).resolve().parents[1] / "presets"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PresetError(f"Unreadable preset {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PresetError(f"Preset root must be an object: {path}")
    return raw


def _merge_json(base: Any, overlay: Any) -> Any:
    """Small deterministic merge used only for preset composition.

    Objects merge recursively. Arrays append unique items while preserving first-seen
    order. Scalars from the later component/overlay replace earlier values. This is
    deliberately much smaller than a general configuration language.
    """
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = deepcopy(base)
        for key, value in overlay.items():
            if key in merged:
                merged[key] = _merge_json(merged[key], value)
            else:
                merged[key] = deepcopy(value)
        return merged
    if isinstance(base, list) and isinstance(overlay, list):
        # When both sides are object lists keyed by a stable `id`, merge matching
        # objects recursively. This keeps composite presets DRY for declarations
        # such as release packages while preserving the simple append-unique
        # behavior for ordinary string/object arrays.
        object_list = all(isinstance(item, dict) and isinstance(item.get("id"), str) for item in (*base, *overlay))
        if object_list:
            merged = deepcopy(base)
            by_id = {str(item["id"]): index for index, item in enumerate(merged)}
            for item in overlay:
                item_id = str(item["id"])
                if item_id in by_id:
                    index = by_id[item_id]
                    merged[index] = _merge_json(merged[index], item)
                else:
                    by_id[item_id] = len(merged)
                    merged.append(deepcopy(item))
            return merged
        merged = deepcopy(base)
        for item in overlay:
            if item not in merged:
                merged.append(deepcopy(item))
        return merged
    return deepcopy(overlay)


def _component_path(component_id: str) -> Path:
    if not _COMPONENT_ID_RE.match(component_id):
        raise PresetError(f"Unsafe preset component id: {component_id!r}")
    return preset_root() / "components" / f"{component_id}.json"


def _load_component(component_id: str, stack: tuple[str, ...]) -> tuple[list[str], dict[str, Any], list[str]]:
    if component_id in stack:
        chain = " -> ".join((*stack, component_id))
        raise PresetError(f"Preset component cycle: {chain}")
    path = _component_path(component_id)
    raw = _read_json(path)
    if raw.get("format") != COMPONENT_FORMAT or int(raw.get("formatVersion") or 0) != PRESET_VERSION:
        raise PresetError(f"Unsupported preset component format/version: {path}")
    if str(raw.get("id") or "").strip() != component_id:
        raise PresetError(f"Preset component id/path mismatch: {path}")

    requirements: list[str] = []
    files: dict[str, Any] = {}
    flattened: list[str] = []
    raw_components = raw.get("components", [])
    if not isinstance(raw_components, list) or any(not isinstance(item, str) for item in raw_components):
        raise PresetError(f"Preset component components must be an array of strings: {path}")
    for child in raw_components:
        child_requirements, child_files, child_flattened = _load_component(child, (*stack, component_id))
        for requirement in child_requirements:
            if requirement not in requirements:
                requirements.append(requirement)
        files = _merge_json(files, child_files)
        for name in child_flattened:
            if name not in flattened:
                flattened.append(name)

    requirements_raw = raw.get("requirements", [])
    if not isinstance(requirements_raw, list) or any(not isinstance(item, str) for item in requirements_raw):
        raise PresetError(f"Preset component requirements must be an array of strings: {path}")
    for requirement in requirements_raw:
        if requirement not in requirements:
            requirements.append(requirement)
    component_files = raw.get("files", {})
    if not isinstance(component_files, dict):
        raise PresetError(f"Preset component files must be an object: {path}")
    files = _merge_json(files, component_files)
    if component_id not in flattened:
        flattened.append(component_id)
    return requirements, files, flattened


def _load(path: Path) -> Preset:
    raw = _read_json(path)
    if raw.get("format") != PRESET_FORMAT or int(raw.get("formatVersion") or 0) != PRESET_VERSION:
        raise PresetError(f"Unsupported preset format/version: {path}")
    preset_id = str(raw.get("id") or "").strip()
    title = str(raw.get("title") or preset_id).strip()
    description = str(raw.get("description") or "").strip()
    if not preset_id:
        raise PresetError(f"Preset id is required: {path}")

    requirements: list[str] = []
    files: dict[str, Any] = {}
    flattened_components: list[str] = []
    components_raw = raw.get("components", [])
    if not isinstance(components_raw, list) or any(not isinstance(item, str) for item in components_raw):
        raise PresetError(f"Preset components must be an array of strings: {path}")
    for component_id in components_raw:
        component_requirements, component_files, component_flattened = _load_component(component_id, ())
        for requirement in component_requirements:
            if requirement not in requirements:
                requirements.append(requirement)
        files = _merge_json(files, component_files)
        for name in component_flattened:
            if name not in flattened_components:
                flattened_components.append(name)

    requirements_raw = raw.get("requirements", [])
    if not isinstance(requirements_raw, list) or any(not isinstance(item, str) for item in requirements_raw):
        raise PresetError(f"Preset requirements must be an array of strings: {path}")
    for requirement in requirements_raw:
        if requirement not in requirements:
            requirements.append(requirement)

    local_files = raw.get("files", {})
    if not isinstance(local_files, dict):
        raise PresetError(f"Preset files must be an object: {path}")
    files = _merge_json(files, local_files)
    if not files:
        raise PresetError(f"Preset files are required after composition: {path}")

    return Preset(
        preset_id=preset_id,
        title=title,
        description=description,
        requirements=tuple(requirements),
        files=files,
        path=path,
        components=tuple(flattened_components),
    )


def list_presets() -> list[Preset]:
    root = preset_root()
    if not root.is_dir():
        return []
    presets = [_load(path) for path in sorted(root.glob("*.json"))]
    ids = [item.preset_id for item in presets]
    if len(ids) != len(set(ids)):
        raise PresetError("Duplicate preset id detected")
    return presets


def get_preset(preset_id: str) -> Preset:
    for preset in list_presets():
        if preset.preset_id == preset_id:
            return preset
    raise PresetError(f"Unknown preset: {preset_id}")


def apply_preset(preset: Preset, project_root: Path, *, force: bool = False) -> list[Path]:
    project_root = project_root.resolve()
    destinations: list[tuple[Path, Any]] = []
    for rel, content in preset.files.items():
        rel_path = Path(str(rel))
        if rel_path.is_absolute() or ".." in rel_path.parts:
            raise PresetError(f"Unsafe preset destination: {rel}")
        target = (project_root / rel_path).resolve()
        try:
            target.relative_to(project_root)
        except ValueError as exc:
            raise PresetError(f"Preset destination escapes project root: {rel}") from exc
        if target.exists() and not force:
            raise PresetError(f"Refusing to overwrite existing file without --force: {target}")
        destinations.append((target, content))

    written: list[Path] = []
    for target, content in destinations:
        if not isinstance(content, (dict, list)):
            raise PresetError(f"Preset file content must be JSON-compatible object/array: {target.name}")
        atomic_json_write(target, content)
        written.append(target)
    return written
