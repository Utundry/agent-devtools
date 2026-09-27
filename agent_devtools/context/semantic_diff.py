from __future__ import annotations

from dataclasses import dataclass

from .chunks import Chunk, ChunkError, chunk_text, semantic_chunk
from .config import ContextConfig
from .index import ContextIndexError, discover_files
from .semantic_map import SemanticMapError, load_semantic_map, resolve_mapping

_STABLE_PREFIXES = ("symbol:", "semantic:", "struct:", "heading:")


@dataclass(frozen=True)
class SemanticState:
    identity: str
    path: str
    kind: str
    label: str
    selector: str | None
    start_line: int
    end_line: int
    content_sha256: str

    def payload(self) -> dict:
        return {
            "identity": self.identity,
            "path": self.path,
            "kind": self.kind,
            "label": self.label,
            "selector": self.selector,
            "startLine": self.start_line,
            "endLine": self.end_line,
            "contentSha256": self.content_sha256,
        }


def _state(chunk: Chunk) -> SemanticState:
    return SemanticState(
        chunk.chunk_id,
        chunk.path,
        chunk.kind,
        chunk.label,
        chunk.selector,
        chunk.start_line,
        chunk.end_line,
        chunk.content_sha256,
    )


def _add_state(result: dict[str, SemanticState], state: SemanticState) -> None:
    previous = result.get(state.identity)
    if previous is not None:
        raise ContextIndexError(
            f"duplicate stable context identity {state.identity}: {previous.path}, {state.path}"
        )
    result[state.identity] = state


def _stable_chunks(config: ContextConfig) -> dict[str, SemanticState]:
    if not config.root.is_dir():
        raise ContextIndexError(f"semantic diff source tree does not exist or is not a directory: {config.root}")
    try:
        semantic_map = load_semantic_map(config.root, config.semantic_map)
        files = discover_files(config)
    except SemanticMapError as exc:
        raise ContextIndexError(str(exc)) from exc

    result: dict[str, SemanticState] = {}
    for rel, info in files.items():
        if not info.is_text:
            continue
        try:
            text = (config.root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            chunks = chunk_text(
                rel,
                text,
                chunk_lines=config.chunk_lines,
                overlap_lines=config.overlap_lines,
            )
        except ChunkError as exc:
            raise ContextIndexError(str(exc)) from exc
        for chunk in chunks:
            if chunk.chunk_id.startswith(_STABLE_PREFIXES):
                _add_state(result, _state(chunk))

    for mapping in semantic_map.mappings:
        try:
            content, start, end = resolve_mapping(config.root, mapping)
        except SemanticMapError as exc:
            raise ContextIndexError(str(exc)) from exc
        chunk = semantic_chunk(
            mapping.file,
            mapping.semantic_id,
            f"{mapping.selector_type}:{mapping.selector_value}",
            content,
            start,
            end,
        )
        _add_state(result, _state(chunk))
    return result


def compare_semantic_states(
    before: ContextConfig,
    after: ContextConfig,
    *,
    include_unchanged: bool = False,
) -> dict:
    old = _stable_chunks(before)
    new = _stable_chunks(after)
    changes: list[dict] = []
    counts = {"added": 0, "removed": 0, "modified": 0, "unchanged": 0}
    for identity in sorted(set(old) | set(new)):
        left, right = old.get(identity), new.get(identity)
        if left is None:
            status = "added"
        elif right is None:
            status = "removed"
        elif left.content_sha256 != right.content_sha256:
            status = "modified"
        else:
            status = "unchanged"
        counts[status] += 1
        if status == "unchanged" and not include_unchanged:
            continue
        changes.append(
            {
                "identity": identity,
                "status": status,
                "before": left.payload() if left else None,
                "after": right.payload() if right else None,
            }
        )
    return {
        "format": "agent-devtools-semantic-diff",
        "formatVersion": 1,
        "beforeRoot": str(before.root),
        "afterRoot": str(after.root),
        "counts": counts,
        "reportedChanges": len(changes),
        "includesUnchanged": include_unchanged,
        "changes": changes,
    }
