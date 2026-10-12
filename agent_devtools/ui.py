from __future__ import annotations

import json
from pathlib import Path


VERBOSITY_COMPACT = "compact"
VERBOSITY_VERBOSE = "verbose"
VERBOSITY_VALUES = {VERBOSITY_COMPACT, VERBOSITY_VERBOSE}


def load_verbosity(root: Path) -> str:
    """Return the project human-output verbosity. Machine JSON is unaffected.

    Missing, unreadable, or unsupported presentation settings fail soft to the
    compact mode so a cosmetic configuration problem never blocks routine work.
    """
    path = root.resolve() / "agent-tools.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return VERBOSITY_COMPACT
    if not isinstance(raw, dict):
        return VERBOSITY_COMPACT
    ui = raw.get("ui")
    if not isinstance(ui, dict):
        return VERBOSITY_COMPACT
    value = str(ui.get("verbosity") or "").strip().lower()
    return value if value in VERBOSITY_VALUES else VERBOSITY_COMPACT


def is_verbose(root: Path) -> bool:
    return load_verbosity(root) == VERBOSITY_VERBOSE
