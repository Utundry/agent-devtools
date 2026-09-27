from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def atomic_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def read_tail_text(path: Path, max_bytes: int = 512 * 1024) -> str:
    if not path.is_file():
        return ""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(-max_bytes, 2)
        return handle.read().decode("utf-8", errors="replace")
