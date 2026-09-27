from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .hashing import sha256_file, stable_fingerprint


@lru_cache(maxsize=1)
def engine_fingerprint() -> str:
    """Fingerprint the portable Python engine that interprets project policy/config.

    Certified evidence must never survive a change to the verifier itself merely because
    project inputs stayed byte-identical.  The engine is small enough that hashing its
    Python sources is cheap, deterministic and independent of package installation state.
    """
    package_root = Path(__file__).resolve().parents[1]
    entries: list[tuple[str, str]] = []
    for path in sorted(package_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        entries.append((path.relative_to(package_root).as_posix(), sha256_file(path)))
    return stable_fingerprint({"schema": "agent-devtools-engine-v1", "files": entries})
