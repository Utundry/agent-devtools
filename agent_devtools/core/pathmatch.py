from __future__ import annotations

import fnmatch
import re
from functools import lru_cache
from typing import Iterable


@lru_cache(maxsize=512)
def _recursive_pattern(pattern: str):
    parts = pattern.split("**/")
    bodies = [fnmatch.translate(part)[4:-3] for part in parts]
    return re.compile("(?:.*/)?".join(bodies), re.DOTALL)


def matches_pattern(path: str, pattern: str) -> bool:
    """Match project-relative POSIX paths with lightweight ** convenience rules.

    fnmatch already treats '/' as an ordinary character.  The explicit suffix/prefix
    handling makes common project patterns such as ``dir/**`` and ``**/name`` behave
    naturally for the directory root itself as well as descendants.
    """
    path = path.replace("\\", "/")
    pattern = pattern.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    while pattern.startswith("./"):
        pattern = pattern[2:]
    if pattern in {"", "**", "**/*"}:
        return True
    if fnmatch.fnmatchcase(path, pattern):
        return True
    if "**/" in pattern and _recursive_pattern(pattern).fullmatch(path):
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
