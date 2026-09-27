from __future__ import annotations

import fnmatch
from typing import Iterable


def matches_pattern(path: str, pattern: str) -> bool:
    """Match project-relative POSIX paths with lightweight ** convenience rules.

    fnmatch already treats '/' as an ordinary character.  The explicit suffix/prefix
    handling makes common project patterns such as ``dir/**`` and ``**/name`` behave
    naturally for the directory root itself as well as descendants.
    """
    path = path.replace("\\", "/").lstrip("./")
    pattern = pattern.replace("\\", "/").lstrip("./")
    if pattern in {"", "**", "**/*"}:
        return True
    if fnmatch.fnmatchcase(path, pattern):
        return True
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        if path == prefix or path.startswith(prefix + "/"):
            return True
    if pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:]):
        return True
    return False


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(matches_pattern(path, pattern) for pattern in patterns)
