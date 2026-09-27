from __future__ import annotations

import configparser
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11 is not a supported dev runtime today
    tomllib = None  # type: ignore[assignment]

from agent_devtools.core.hashing import sha256_file, stable_fingerprint


class SemanticMapError(RuntimeError):
    pass


@dataclass(frozen=True)
class SemanticMapping:
    semantic_id: str
    file: str
    selector_type: str
    selector_value: str


@dataclass(frozen=True)
class SemanticMap:
    path: Path | None
    fingerprint: str
    mappings: tuple[SemanticMapping, ...]

    @property
    def target_files(self) -> tuple[str, ...]:
        return tuple(sorted({item.file for item in self.mappings}))


def _normalized_file(raw: str) -> str:
    value = raw.replace("\\", "/").lstrip("./")
    if not value or value.startswith("/") or ".." in Path(value).parts:
        raise SemanticMapError(f"semantic map file must stay inside project root: {raw!r}")
    return value


def load_semantic_map(root: Path, path: Path | None) -> SemanticMap:
    if path is None or not path.is_file():
        return SemanticMap(path, stable_fingerprint({"schema": "semantic-map-v1", "mappings": []}), ())
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SemanticMapError(f"semantic map is unreadable: {exc}") from exc
    if not isinstance(raw, dict) or int(raw.get("version") or 0) != 1:
        raise SemanticMapError("semantic map must be an object with version=1")
    rows = raw.get("semanticRegions", [])
    if not isinstance(rows, list):
        raise SemanticMapError("semanticRegions must be an array")
    seen: set[str] = set()
    mappings: list[SemanticMapping] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SemanticMapError(f"semanticRegions[{index}] must be an object")
        semantic_id = str(row.get("id") or "").strip()
        file = _normalized_file(str(row.get("file") or "").strip())
        selector = row.get("selector")
        if not semantic_id or not isinstance(selector, dict):
            raise SemanticMapError(f"semanticRegions[{index}] requires id, file and selector")
        if semantic_id in seen:
            raise SemanticMapError(f"duplicate semantic map id: {semantic_id}")
        seen.add(semantic_id)
        selector_type = str(selector.get("type") or "").strip()
        selector_value = str(selector.get("value") or "").strip()
        if selector_type not in {"json-pointer", "toml-path", "ini-path", "whole-file"}:
            raise SemanticMapError(f"unsupported semantic selector type: {selector_type!r}")
        if selector_type != "whole-file" and not selector_value:
            raise SemanticMapError(f"semanticRegions[{index}].selector.value is required")
        mappings.append(SemanticMapping(semantic_id, file, selector_type, selector_value))
    fp = stable_fingerprint({
        "schema": "semantic-map-v1",
        "fileSha256": sha256_file(path),
        "mappings": [item.__dict__ for item in mappings],
    })
    return SemanticMap(path, fp, tuple(mappings))


def _json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise SemanticMapError(f"invalid JSON pointer: {pointer!r}")
    current = document
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise SemanticMapError(f"JSON pointer target missing: {pointer!r}") from exc
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise SemanticMapError(f"JSON pointer target missing: {pointer!r}")
    return current


def _path_value(document: Any, path: str, *, label: str) -> Any:
    current = document
    for token in path.split("."):
        if not token or not isinstance(current, dict) or token not in current:
            raise SemanticMapError(f"{label} target missing: {path!r}")
        current = current[token]
    return current


def resolve_mapping(root: Path, mapping: SemanticMapping) -> tuple[str, int, int]:
    target = root / mapping.file
    if not target.is_file():
        raise SemanticMapError(f"semantic map target missing: {mapping.file}")
    if mapping.selector_type == "whole-file":
        data = target.read_bytes()
        return f"opaque resource {mapping.file} · {len(data)} bytes · sha256={__import__('hashlib').sha256(data).hexdigest()}\n", 1, 1
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SemanticMapError(f"semantic selector target must be UTF-8 text: {mapping.file}") from exc
    suffix = target.suffix.lower()
    if mapping.selector_type == "json-pointer":
        if suffix != ".json":
            raise SemanticMapError(f"json-pointer selector requires .json target: {mapping.file}")
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SemanticMapError(f"JSON target is invalid: {mapping.file}: {exc}") from exc
        value = _json_pointer(document, mapping.selector_value)
        content = (value + "\n") if isinstance(value, str) else (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    elif mapping.selector_type == "toml-path":
        if tomllib is None:
            raise SemanticMapError("toml-path requires Python stdlib tomllib")
        try:
            document = tomllib.loads(text)
        except Exception as exc:
            raise SemanticMapError(f"TOML target is invalid: {mapping.file}: {exc}") from exc
        value = _path_value(document, mapping.selector_value, label="TOML path")
        content = repr(value) + "\n"
    elif mapping.selector_type == "ini-path":
        if "." not in mapping.selector_value:
            raise SemanticMapError("ini-path selector must use section.key")
        section, key = mapping.selector_value.rsplit(".", 1)
        parser = configparser.ConfigParser()
        try:
            parser.read_string(text)
        except configparser.Error as exc:
            raise SemanticMapError(f"INI target is invalid: {mapping.file}: {exc}") from exc
        if not parser.has_option(section, key):
            raise SemanticMapError(f"INI path target missing: {mapping.selector_value!r}")
        content = parser.get(section, key) + "\n"
    else:  # pragma: no cover - guarded by load
        raise SemanticMapError(f"unsupported selector: {mapping.selector_type}")
    needle = mapping.selector_value.split("/")[-1].replace("~1", "/").replace("~0", "~").split(".")[-1]
    line = 1
    for index, raw in enumerate(text.splitlines(), start=1):
        if needle and needle in raw:
            line = index
            break
    return content, line, line
