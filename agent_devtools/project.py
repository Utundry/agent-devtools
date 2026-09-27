from __future__ import annotations

from pathlib import Path


def discover_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "agent-tools.json").is_file() or (candidate / ".git").exists():
            return candidate
    return current
