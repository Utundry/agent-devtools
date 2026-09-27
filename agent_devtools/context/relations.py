from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from .semantic import SemanticRelation, extract_profile_relations, extract_semantic_document


def _candidate_paths(source: str, target: str) -> list[str]:
    base = Path(source).parent
    raw = (base / target).as_posix()
    candidates = [raw]
    if not Path(raw).suffix:
        for ext in (".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".php", ".java", ".cs", ".kt", ".rs"):
            candidates.append(raw + ext)
        for name in ("index.js", "index.ts", "index.tsx", "index.vue", "__init__.py"):
            candidates.append((Path(raw) / name).as_posix())
    return candidates


def _python_relations(path: str, text: str) -> tuple[SemanticRelation, ...]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ()
    found: list[SemanticRelation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            prefix = "./" if node.level == 1 else ("../" * (node.level - 1))
            module = (node.module or "").replace(".", "/")
            found.append(SemanticRelation("import:path", prefix + module, int(getattr(node, "lineno", 1))))
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(SemanticRelation("import:symbol", node.module, int(getattr(node, "lineno", 1))))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.append(SemanticRelation("import:symbol", alias.name, int(getattr(node, "lineno", 1))))
    return tuple(found)


def extract_normalized_relations(path: str, text: str) -> tuple[SemanticRelation, ...]:
    if Path(path).suffix.lower() == ".py":
        return _python_relations(path, text)
    return extract_profile_relations(path, text)


def build_definition_index(documents: dict[str, str]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for path, text in documents.items():
        document = extract_semantic_document(path, text)
        if document is None:
            continue
        for definition in document.definitions:
            index[definition.qualified_name.casefold()].add(path)
            index[definition.name.casefold()].add(path)
    return dict(index)


def _symbol_tail(target: str) -> str:
    value = target.replace("\\", ".").replace("::", ".").replace("/", ".")
    return value.rstrip(".*").split(".")[-1].casefold()


def resolve_relation_targets(
    source: str,
    relations: tuple[SemanticRelation, ...],
    known_paths: set[str],
    definition_index: dict[str, set[str]] | None = None,
) -> set[tuple[str, str]]:
    resolved: set[tuple[str, str]] = set()
    definitions = definition_index or {}
    for relation in relations:
        base_kind, _, target_type = relation.kind.partition(":")
        if target_type == "path":
            target = relation.target
            if target.startswith("."):
                probes = _candidate_paths(source, target)
            elif target.startswith("/"):
                stem = target[1:]
                probes = [stem, stem + ".py", stem + ".js", stem + ".ts", (Path(stem) / "__init__.py").as_posix()]
            else:
                # package/external imports are intentionally ignored unless the file is visibly local
                probes = [target]
            for probe in probes:
                normalized = Path(probe).as_posix().lstrip("./")
                if normalized in known_paths:
                    resolved.add((normalized, base_kind))
                    break
        else:
            raw = relation.target.replace("\\", ".").replace("::", ".")
            keys = (raw.casefold(), _symbol_tail(raw))
            candidates: set[str] = set()
            for key in keys:
                candidates.update(definitions.get(key, set()))
            if len(candidates) == 1:
                target_path = next(iter(candidates))
                if target_path != source:
                    resolved.add((target_path, base_kind))
    return resolved


def extract_relations(
    path: str,
    text: str,
    known_paths: set[str],
    *,
    definition_index: dict[str, set[str]] | None = None,
) -> set[tuple[str, str]]:
    return resolve_relation_targets(path, extract_normalized_relations(path, text), known_paths, definition_index)
