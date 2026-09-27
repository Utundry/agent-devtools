from __future__ import annotations

import ast
import configparser
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .semantic import extract_semantic_document

try:
    import tomllib
except ImportError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

_COMMENT_PREFIX = r"(?:/{2,}|#+|--+|;|/\*+|\*+|<!--)"
_BEGIN_RE = re.compile(rf"^\s*{_COMMENT_PREFIX}\s*@semantic-begin\s+([A-Za-z][A-Za-z0-9_.:-]*)")
_END_RE = re.compile(rf"^\s*{_COMMENT_PREFIX}\s*@semantic-end\s+([A-Za-z][A-Za-z0-9_.:-]*)")
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_DECL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:class|interface|trait|function)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)


class ChunkError(RuntimeError):
    pass


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    path: str
    kind: str
    label: str
    anchor: str | None
    selector: str | None
    start_line: int
    end_line: int
    content: str
    content_sha256: str


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chunk_id(
    path: str,
    kind: str,
    label: str,
    start: int,
    end: int,
    content_sha: str,
    *,
    anchor: str | None = None,
    stable_id: str | None = None,
) -> str:
    if anchor:
        return f"semantic:{anchor}"
    if stable_id:
        return stable_id
    raw = f"{path}\0{kind}\0{label}\0{start}\0{end}\0{content_sha}".encode("utf-8")
    return f"chunk:{hashlib.sha256(raw).hexdigest()[:24]}"


def _make(
    lines: list[str],
    path: str,
    kind: str,
    label: str,
    start: int,
    end: int,
    anchor: str | None = None,
    *,
    stable_id: str | None = None,
    selector: str | None = None,
    content_override: str | None = None,
) -> Chunk:
    content = content_override if content_override is not None else "\n".join(lines[start - 1:end]).rstrip() + "\n"
    digest = _sha(content)
    return Chunk(
        _chunk_id(path, kind, label, start, end, digest, anchor=anchor, stable_id=stable_id),
        path,
        kind,
        label,
        anchor,
        selector,
        start,
        end,
        content,
        digest,
    )


def semantic_chunk(path: str, semantic_id: str, selector: str, content: str, start: int = 1, end: int = 1) -> Chunk:
    digest = _sha(content)
    return Chunk(
        f"semantic:{semantic_id}", path, "semantic-selector", semantic_id, semantic_id,
        selector, start, end, content, digest,
    )


def _explicit_chunks(lines: list[str], path: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    open_anchor: tuple[str, int] | None = None
    seen: set[str] = set()
    for lineno, line in enumerate(lines, start=1):
        begin = _BEGIN_RE.search(line)
        end = _END_RE.search(line)
        if begin:
            if open_anchor is not None:
                raise ChunkError(f"nested semantic anchor in {path}:{lineno}")
            anchor = begin.group(1)
            if anchor in seen:
                raise ChunkError(f"duplicate semantic anchor {anchor!r} in {path}")
            seen.add(anchor)
            open_anchor = (anchor, lineno)
        if end:
            anchor = end.group(1)
            if open_anchor is None or open_anchor[0] != anchor:
                raise ChunkError(f"unmatched semantic end {anchor!r} in {path}:{lineno}")
            name, start = open_anchor
            chunks.append(_make(lines, path, "semantic", name, start, lineno, name))
            open_anchor = None
    if open_anchor is not None:
        raise ChunkError(f"unclosed semantic anchor {open_anchor[0]!r} in {path}:{open_anchor[1]}")
    return chunks


def _slug(value: str) -> str:
    value = re.sub(r"\s+", "-", value.strip().casefold())
    value = re.sub(r"[^a-z0-9_.:-]+", "-", value).strip("-")
    return value or "section"


def _markdown_chunks(lines: list[str], path: str) -> list[Chunk]:
    headings: list[tuple[int, str]] = []
    for lineno, line in enumerate(lines, start=1):
        match = _MD_HEADING_RE.match(line)
        if match:
            headings.append((lineno, match.group(2).strip()))
    counts: dict[str, int] = {}
    result: list[Chunk] = []
    for index, (start, label) in enumerate(headings):
        end = (headings[index + 1][0] - 1) if index + 1 < len(headings) else len(lines)
        slug = _slug(label)
        counts[slug] = counts.get(slug, 0) + 1
        suffix = f":{counts[slug]}" if counts[slug] > 1 else ""
        result.append(_make(lines, path, "heading", label, start, end, stable_id=f"heading:{path}#{slug}{suffix}"))
    return result


def _python_chunks(text: str, lines: list[str], path: str) -> list[Chunk]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    result: list[Chunk] = []

    def walk(body: list[ast.stmt], prefix: tuple[str, ...]) -> None:
        for node in body:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            start = int(getattr(node, "lineno", 0) or 0)
            end = int(getattr(node, "end_lineno", start) or start)
            if start <= 0 or end < start:
                continue
            qualified = ".".join((*prefix, str(node.name)))
            kind = "class" if isinstance(node, ast.ClassDef) else ("method" if prefix else "function")
            result.append(_make(
                lines, path, kind, qualified, start, min(end, len(lines)),
                stable_id=f"symbol:{path}::{qualified}",
            ))
            nested = getattr(node, "body", None)
            if isinstance(nested, list):
                walk(nested, (*prefix, str(node.name)))

    walk(tree.body, ())
    return result


def _semantic_declaration_chunks(text: str, lines: list[str], path: str) -> list[Chunk]:
    document = extract_semantic_document(path, text)
    if document is None:
        return []
    result: list[Chunk] = []
    counts: dict[str, int] = {}
    for definition in document.definitions:
        label = definition.qualified_name
        counts[label] = counts.get(label, 0) + 1
        suffix = f":{counts[label]}" if counts[label] > 1 else ""
        result.append(_make(
            lines, path, definition.kind, label, definition.start_line, definition.end_line,
            stable_id=f"symbol:{path}::{label}{suffix}",
        ))
    return result


def _json_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _structured_line(lines: list[str], key: str) -> int:
    for index, line in enumerate(lines, start=1):
        if key and key in line:
            return index
    return 1


def _structured_chunks(text: str, lines: list[str], path: str) -> list[Chunk]:
    suffix = Path(path).suffix.lower()
    result: list[Chunk] = []
    if suffix == ".json":
        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            return []
        if not isinstance(document, dict):
            return []
        for key, value in document.items():
            pointer = "/" + _json_pointer_token(str(key))
            line = _structured_line(lines, f'"{key}"')
            content = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            result.append(_make(lines, path, "json", pointer, line, line, stable_id=f"struct:{path}#{pointer}", selector=f"json-pointer:{pointer}", content_override=content))
            if isinstance(value, dict):
                for child, child_value in value.items():
                    child_pointer = pointer + "/" + _json_pointer_token(str(child))
                    child_line = _structured_line(lines, f'"{child}"')
                    child_content = json.dumps(child_value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
                    result.append(_make(lines, path, "json", child_pointer, child_line, child_line, stable_id=f"struct:{path}#{child_pointer}", selector=f"json-pointer:{child_pointer}", content_override=child_content))
    elif suffix == ".toml" and tomllib is not None:
        try:
            document = tomllib.loads(text)
        except Exception:
            return []
        for key, value in document.items():
            selector = str(key)
            line = _structured_line(lines, f"[{key}]")
            result.append(_make(lines, path, "toml", selector, line, line, stable_id=f"struct:{path}::{selector}", selector=f"toml-path:{selector}", content_override=repr(value) + "\n"))
            if isinstance(value, dict):
                for child, child_value in value.items():
                    child_selector = f"{key}.{child}"
                    child_line = _structured_line(lines, child)
                    result.append(_make(lines, path, "toml", child_selector, child_line, child_line, stable_id=f"struct:{path}::{child_selector}", selector=f"toml-path:{child_selector}", content_override=repr(child_value) + "\n"))
    elif suffix in {".ini", ".cfg"}:
        parser = configparser.ConfigParser()
        try:
            parser.read_string(text)
        except configparser.Error:
            return []
        for section in parser.sections():
            for key, value in parser.items(section):
                selector = f"{section}.{key}"
                line = _structured_line(lines, key)
                result.append(_make(lines, path, "ini", selector, line, line, stable_id=f"struct:{path}::{selector}", selector=f"ini-path:{selector}", content_override=value + "\n"))
    return result



def _knowledge_chunks(text: str, lines: list[str], path: str) -> list[Chunk]:
    if not path.startswith(".agent-knowledge/") or not path.endswith(".json"):
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict) or payload.get("format") != "agent-devtools-project-knowledge":
        return []
    record_id = str(payload.get("id") or "").strip()
    kind = str(payload.get("kind") or "knowledge").strip()
    subject = str(payload.get("subject") or "").strip()
    statement = str(payload.get("statement") or "").strip()
    status = str(payload.get("status") or "").strip()
    if not record_id or not subject or not statement:
        return []
    anchors = ", ".join(str(x) for x in payload.get("anchors", []) if str(x).strip())
    rendered = f"{kind}: {subject}\n{statement}\n"
    if kind == "source":
        url = str(payload.get("url") or "").strip()
        accessed = str(payload.get("accessedAtUtc") or "").strip()
        claims = [str(x).strip() for x in payload.get("claims", []) if str(x).strip()]
        if url:
            rendered += f"url: {url}\n"
        if accessed:
            rendered += f"accessed: {accessed}\n"
        if claims:
            rendered += "claims: " + "; ".join(claims) + "\n"
    if anchors:
        rendered += f"anchors: {anchors}\n"
    source_refs = [str(x).strip() for x in payload.get("sourceRefs", []) if str(x).strip()]
    if source_refs:
        rendered += "sources: " + ", ".join(source_refs) + "\n"
    created_by = payload.get("createdBy")
    if isinstance(created_by, dict):
        parts = []
        for key, label in (("author", "author"), ("agentEnvironment", "agent"), ("workstation", "workstation")):
            value = str(created_by.get(key) or "").strip()
            if value:
                parts.append(f"{label}={value}")
        if parts:
            rendered += "provenance: " + ", ".join(parts) + "\n"
    task_goal = str(payload.get("taskGoal") or "").strip()
    if task_goal:
        rendered += f"provenance task: {task_goal}\n"
    return [_make(lines, path, "knowledge", subject, 1, max(1, len(lines)), stable_id=f"knowledge:{record_id}", selector=f"knowledge-id:{record_id}", content_override=rendered)]

def _windows(lines: list[str], path: str, chunk_lines: int, overlap_lines: int) -> list[Chunk]:
    if not lines:
        return []
    result: list[Chunk] = []
    step = max(1, chunk_lines - overlap_lines)
    start = 1
    while start <= len(lines):
        end = min(len(lines), start + chunk_lines - 1)
        result.append(_make(lines, path, "window", f"lines {start}-{end}", start, end))
        if end == len(lines):
            break
        start += step
    return result


def chunk_text(path: str, text: str, *, chunk_lines: int, overlap_lines: int) -> list[Chunk]:
    lines = text.splitlines()
    result = _explicit_chunks(lines, path)
    result.extend(_knowledge_chunks(text, lines, path))
    suffix = Path(path).suffix.lower()
    if suffix == ".md":
        result.extend(_markdown_chunks(lines, path))
    elif suffix == ".py":
        result.extend(_python_chunks(text, lines, path))
    else:
        result.extend(_semantic_declaration_chunks(text, lines, path))
    if suffix in {".json", ".toml", ".ini", ".cfg"} and not path.startswith(".agent-knowledge/"):
        result.extend(_structured_chunks(text, lines, path))
    result.extend(_windows(lines, path, chunk_lines, overlap_lines))

    dedup: list[Chunk] = []
    seen: set[tuple[str, int, int, str]] = set()
    seen_ids: set[str] = set()
    for chunk in result:
        key = (chunk.kind, chunk.start_line, chunk.end_line, chunk.content_sha256)
        if key in seen or chunk.chunk_id in seen_ids:
            continue
        seen.add(key)
        seen_ids.add(chunk.chunk_id)
        dedup.append(chunk)
    return dedup
