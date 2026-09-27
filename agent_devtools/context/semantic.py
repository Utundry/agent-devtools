from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SemanticDefinition:
    kind: str
    name: str
    qualified_name: str
    parent: str | None
    start_line: int
    end_line: int


@dataclass(frozen=True)
class SemanticRelation:
    kind: str
    target: str
    source_line: int


@dataclass(frozen=True)
class SemanticDocument:
    path: str
    language: str
    definitions: tuple[SemanticDefinition, ...]
    relations: tuple[SemanticRelation, ...] = ()


@dataclass(frozen=True)
class RelationPattern:
    kind: str
    target_type: str  # path | symbol
    pattern: str


@dataclass(frozen=True)
class LanguageProfile:
    language: str
    family: str
    extensions: tuple[str, ...]
    modifiers: frozenset[str]
    declarations: dict[str, str]
    relations: tuple[RelationPattern, ...] = ()


_COMMON_MODIFIERS = frozenset({
    "public", "protected", "private", "static", "final", "abstract", "readonly",
    "export", "default", "async", "declare", "native", "sealed", "open", "internal",
    "override", "virtual", "extern", "unsafe", "partial", "const", "inline", "pub",
})

_PHP_RELATIONS = (
    RelationPattern("import", "symbol", r"(?m)^\s*use\s+(?P<target>[A-Za-z_\\][A-Za-z0-9_\\]*)"),
    RelationPattern("extends", "symbol", r"\bextends\s+(?P<target>[A-Za-z_\\][A-Za-z0-9_\\]*)"),
    RelationPattern("implements", "symbol", r"\bimplements\s+(?P<target>[A-Za-z_\\][A-Za-z0-9_\\]*)"),
    RelationPattern("include", "path", r"\b(?:require|include)(?:_once)?\s*\(?\s*['\"](?P<target>[^'\"]+)['\"]"),
)
_JS_RELATIONS = (
    RelationPattern("import", "path", r"(?:from\s+|require\s*\(\s*|import\s*\(\s*)['\"](?P<target>[^'\"]+)['\"]"),
    RelationPattern("extends", "symbol", r"\bextends\s+(?P<target>[A-Za-z_$][A-Za-z0-9_$.]*)"),
    RelationPattern("implements", "symbol", r"\bimplements\s+(?P<target>[A-Za-z_$][A-Za-z0-9_$.]*)"),
)
_JAVA_RELATIONS = (
    RelationPattern("import", "symbol", r"(?m)^\s*import\s+(?P<target>[A-Za-z_$][A-Za-z0-9_$.]*)\s*;"),
    RelationPattern("extends", "symbol", r"\bextends\s+(?P<target>[A-Za-z_$][A-Za-z0-9_$.]*)"),
    RelationPattern("implements", "symbol", r"\bimplements\s+(?P<target>[A-Za-z_$][A-Za-z0-9_$.]*)"),
)
_CS_RELATIONS = (
    RelationPattern("import", "symbol", r"(?m)^\s*using\s+(?P<target>[A-Za-z_][A-Za-z0-9_.]*)\s*;"),
    RelationPattern("extends", "symbol", r"\:\s*(?P<target>[A-Za-z_][A-Za-z0-9_.]*)"),
)
_KOTLIN_RELATIONS = (
    RelationPattern("import", "symbol", r"(?m)^\s*import\s+(?P<target>[A-Za-z_][A-Za-z0-9_.]*)"),
    RelationPattern("extends", "symbol", r"\:\s*(?P<target>[A-Za-z_][A-Za-z0-9_.]*)"),
)
_RUST_RELATIONS = (
    RelationPattern("import", "symbol", r"(?m)^\s*use\s+(?P<target>[A-Za-z_][A-Za-z0-9_:]*)"),
)

_PROFILES = (
    LanguageProfile("php", "brace", (".php",), _COMMON_MODIFIERS,
                    {"class": "class", "interface": "interface", "trait": "trait", "enum": "enum", "function": "function"}, _PHP_RELATIONS),
    LanguageProfile("javascript", "brace", (".js", ".jsx"), _COMMON_MODIFIERS,
                    {"class": "class", "function": "function"}, _JS_RELATIONS),
    LanguageProfile("typescript", "brace", (".ts", ".tsx", ".vue"), _COMMON_MODIFIERS,
                    {"class": "class", "interface": "interface", "enum": "enum", "function": "function"}, _JS_RELATIONS),
    LanguageProfile("java", "brace", (".java",), _COMMON_MODIFIERS,
                    {"class": "class", "interface": "interface", "enum": "enum"}, _JAVA_RELATIONS),
    LanguageProfile("csharp", "brace", (".cs",), _COMMON_MODIFIERS,
                    {"class": "class", "interface": "interface", "enum": "enum"}, _CS_RELATIONS),
    LanguageProfile("kotlin", "brace", (".kt", ".kts"), _COMMON_MODIFIERS,
                    {"class": "class", "interface": "interface", "enum": "enum", "fun": "function"}, _KOTLIN_RELATIONS),
    LanguageProfile("rust", "brace", (".rs",), _COMMON_MODIFIERS,
                    {"struct": "class", "trait": "interface", "enum": "enum", "fn": "function"}, _RUST_RELATIONS),
)

_BY_EXTENSION = {ext: profile for profile in _PROFILES for ext in profile.extensions}
_IDENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*|[{}();]")


def profile_for_path(path: str) -> LanguageProfile | None:
    return _BY_EXTENSION.get(Path(path).suffix.lower())


def _strip_line_comment(line: str) -> str:
    # This is deliberately lexical, not a parser. It only protects declaration recognition
    # from the common full-line/trailing comment forms used by brace languages.
    stripped = line.lstrip()
    if stripped.startswith(("//", "#", "*")):
        return ""
    for marker in ("//",):
        index = line.find(marker)
        if index >= 0:
            line = line[:index]
    return line


def extract_brace_document(path: str, text: str, profile: LanguageProfile) -> SemanticDocument:
    lines = text.splitlines()
    definitions: list[SemanticDefinition] = []
    # (qualified class/interface name, opening brace depth)
    scopes: list[tuple[str, int]] = []
    brace_depth = 0
    pending_scope: str | None = None

    for lineno, raw in enumerate(lines, start=1):
        line = _strip_line_comment(raw)
        tokens = _IDENT_RE.findall(line)
        declaration: tuple[str, str] | None = None
        if tokens:
            i = 0
            while i < len(tokens) and tokens[i] in profile.modifiers:
                i += 1
            # tolerate annotations/attributes by searching a bounded prefix for the declaration keyword
            search_end = min(len(tokens), i + 8)
            for j in range(i, search_end):
                token = tokens[j]
                if token in profile.declarations and j + 1 < len(tokens):
                    name = tokens[j + 1]
                    if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name):
                        declaration = (profile.declarations[token], name)
                    break

        parent = scopes[-1][0] if scopes else None
        if declaration:
            kind, name = declaration
            qualified = f"{parent}.{name}" if parent and kind == "function" else name
            actual_kind = "method" if parent and kind == "function" else kind
            definitions.append(SemanticDefinition(actual_kind, name, qualified, parent, lineno, lineno))
            if kind in {"class", "interface", "trait", "enum"}:
                pending_scope = qualified

        opens = line.count("{")
        closes = line.count("}")
        if pending_scope is not None and opens:
            scopes.append((pending_scope, brace_depth + 1))
            pending_scope = None
        brace_depth += opens - closes
        while scopes and brace_depth < scopes[-1][1]:
            scopes.pop()

    # Bound each definition to the next declaration or a small maximum window. The stable
    # identity is semantic; physical end_line is navigation/context only.
    bounded: list[SemanticDefinition] = []
    for index, definition in enumerate(definitions):
        next_line = definitions[index + 1].start_line - 1 if index + 1 < len(definitions) else definition.start_line + 119
        end = min(len(lines), max(definition.start_line, next_line))
        bounded.append(SemanticDefinition(
            definition.kind, definition.name, definition.qualified_name, definition.parent,
            definition.start_line, end,
        ))
    return SemanticDocument(path=path, language=profile.language, definitions=tuple(bounded), relations=extract_profile_relations(path, text))



def extract_profile_relations(path: str, text: str) -> tuple[SemanticRelation, ...]:
    profile = profile_for_path(path)
    if profile is None:
        return ()
    found: list[SemanticRelation] = []
    seen: set[tuple[str, str, int]] = set()
    for spec in profile.relations:
        regex = re.compile(spec.pattern)
        for match in regex.finditer(text):
            target = match.group("target").strip()
            line = text.count("\n", 0, match.start()) + 1
            key = (spec.kind, target, line)
            if key in seen:
                continue
            seen.add(key)
            found.append(SemanticRelation(f"{spec.kind}:{spec.target_type}", target, line))
    return tuple(found)

def extract_semantic_document(path: str, text: str) -> SemanticDocument | None:
    profile = profile_for_path(path)
    if profile is None:
        return None
    if profile.family == "brace":
        return extract_brace_document(path, text, profile)
    return None
