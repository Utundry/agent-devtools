from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_devtools.core.io import atomic_json_write


class ProfileError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkProfile:
    profile_id: str
    title: str
    description: str
    development: bool
    verification_mode: str
    change_discovery: str


_PROFILES = {
    "development": WorkProfile("development", "Software development", "Repository development with Git-aware affected checks and release tooling.", True, "check", "git+task"),
    "research": WorkProfile("research", "Research", "Source-driven investigation with explicit findings, assumptions, evidence and conclusions.", False, "record", "task"),
    "analysis": WorkProfile("analysis", "Analysis", "Structured analytical work with explicit inputs, decisions, evidence and review.", False, "record", "task"),
    "document": WorkProfile("document", "Document", "Long-form document work with context, review evidence and durable project knowledge.", False, "record", "task"),
    "general": WorkProfile("general", "General project work", "Neutral agent work without software-development-specific assumptions.", False, "record", "task"),
}


def list_profiles() -> tuple[WorkProfile, ...]:
    return tuple(_PROFILES[key] for key in ("development", "research", "analysis", "document", "general"))


def get_profile(profile_id: str) -> WorkProfile:
    key = str(profile_id or "").strip()
    try:
        return _PROFILES[key]
    except KeyError as exc:
        raise ProfileError(f"unknown work profile: {key or '<empty>'}") from exc


def load_profile(root: Path) -> WorkProfile:
    path = root.resolve() / "agent-tools.json"
    if not path.is_file():
        return _PROFILES["development"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileError(f"cannot read agent-tools.json: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProfileError("agent-tools.json root must be an object")
    work = raw.get("work") or {}
    if not isinstance(work, dict):
        raise ProfileError("agent-tools.json work must be an object")
    return get_profile(str(work.get("profile") or "development"))


def set_profile(root: Path, profile_id: str) -> WorkProfile:
    profile = get_profile(profile_id)
    path = root.resolve() / "agent-tools.json"
    raw: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProfileError(f"cannot read agent-tools.json: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ProfileError("agent-tools.json root must be an object")
        raw = dict(loaded)
    raw["version"] = int(raw.get("version") or 1)
    work = raw.get("work") or {}
    if not isinstance(work, dict):
        raise ProfileError("agent-tools.json work must be an object")
    raw["work"] = {**work, "profile": profile.profile_id}
    atomic_json_write(path, raw)
    return profile
