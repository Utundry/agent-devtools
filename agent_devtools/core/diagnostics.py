from __future__ import annotations

import re

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ERROR_HINT_RE = re.compile(
    r"(?:\bFAIL\b|\bERROR\b|\bError\b|\berror\b|\bTS\d{3,5}\b|Parse error|Fatal error|Traceback|Exception|mismatch)",
    re.IGNORECASE,
)


def trim_to_budget(text: str, budget: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= budget:
        return text
    suffix = "\n… output truncated; see stage log"
    allowed = max(0, budget - len(suffix.encode("utf-8")))
    prefix = raw[:allowed].decode("utf-8", errors="ignore")
    return prefix.rstrip() + suffix


def normalize_diagnostic_line(line: str) -> str:
    return re.sub(r"\s+", " ", ANSI_ESCAPE_RE.sub("", line).strip())


def unique_diagnostics(text: str, *, max_items: int = 5, budget: int = 3000) -> list[str]:
    lines = [normalize_diagnostic_line(line) for line in text.splitlines() if line.strip()]
    candidates = [line for line in lines if ERROR_HINT_RE.search(line)] or lines[-max_items:]
    selected: list[str] = []
    seen: set[str] = set()
    for line in candidates:
        key = line.casefold()
        if not line or key in seen:
            continue
        seen.add(key)
        selected.append(line)
        if len(selected) >= max_items:
            break
    return [line for line in trim_to_budget("\n".join(selected), budget).splitlines() if line.strip()][:max_items]
