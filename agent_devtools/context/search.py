from __future__ import annotations

import hashlib
import math
import re
import sqlite3
from dataclasses import dataclass
from typing import Iterable

from .config import ContextConfig
from .index import ContextIndexError, _connect

_TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)


@dataclass(frozen=True)
class SearchResult:
    chunk_id: str
    path: str
    kind: str
    label: str
    anchor: str | None
    selector: str | None
    start_line: int
    end_line: int
    content: str
    source_kind: str
    weight: float
    priority: str
    score: float
    why: tuple[str, ...]


@dataclass(frozen=True)
class SearchReport:
    query: str
    results: tuple[SearchResult, ...]
    candidate_files: int
    candidate_chunks: int
    estimated_tokens: int
    budget: int
    omitted: dict[str, int]


def _tokens(query: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for token in _TOKEN_RE.findall(query.casefold()):
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def _file_score(row: sqlite3.Row, query: str, tokens: list[str], preferred: set[str], config: ContextConfig) -> float:
    q = query.casefold()
    path = str(row["path"]).casefold()
    synopsis = str(row["synopsis"]).casefold()
    weight = float(row["weight"])
    score = 0.0
    if q and q in path:
        score += 50
    if q and q in synopsis:
        score += 30
    score += sum(10 for token in tokens if token in path)
    score += sum(3 for token in tokens if token in synopsis)
    if str(row["path"]) in preferred:
        score += 35
    score *= weight
    if str(row["priority"]) == "low":
        score *= config.low_priority_factor
    return score


def _score(
    row: sqlite3.Row,
    query: str,
    tokens: list[str],
    order_bonus: float,
    *,
    preferred: set[str],
    config: ContextConfig,
) -> tuple[float, tuple[str, ...]]:
    q = query.casefold().strip()
    path = str(row["path"]).casefold()
    label = str(row["label"]).casefold()
    anchor = str(row["anchor"] or "").casefold()
    selector = str(row["selector"] or "").casefold()
    content = str(row["content"]).casefold()
    weight = float(row["weight"])
    score = order_bonus * weight
    why: list[str] = []
    if q and anchor == q:
        score += 140 * weight
        why.append("exact semantic anchor")
    elif q and q in anchor:
        score += 50 * weight
        why.append("semantic anchor match")
    if q and (q == label or q == selector):
        score += 65 * weight
        why.append("exact label/selector match")
    elif q and q in label:
        score += 35 * weight
        why.append("label phrase match")
    if q and q in path:
        score += 25 * weight
        why.append("path phrase match")
    label_hits = sum(1 for token in tokens if token in label or token in selector)
    path_hits = sum(1 for token in tokens if token in path)
    content_hits = sum(min(content.count(token), 4) for token in tokens)
    if label_hits:
        score += label_hits * 10 * weight
        why.append(f"{label_hits} label/selector token(s)")
    if path_hits:
        score += path_hits * 6 * weight
        why.append(f"{path_hits} path token(s)")
    if content_hits:
        score += content_hits * 1.5 * weight
        why.append(f"{content_hits} content hit(s)")
    kind = str(row["kind"])
    if kind in {"semantic", "semantic-selector"}:
        score += 22 * weight
        why.append("explicit semantic identity")
    elif kind in {"class", "function", "method", "symbol"}:
        score += 14 * weight
        why.append("natural symbol identity")
    elif kind in {"json", "toml", "ini"}:
        score += 11 * weight
        why.append("structured selector")
    elif kind == "heading":
        score += 8 * weight
        why.append("document heading")
    actual_path = str(row["path"])
    if actual_path in preferred:
        score += 25 * weight
        why.append("direct/related affected file")
    if str(row["priority"]) == "low":
        score *= config.low_priority_factor
        why.append("low-priority source penalty")
    if weight != 1.0:
        why.append(f"source weight {weight:g}")
    return score, tuple(why or ["lexical match"])


def _term_set(text: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall(text.casefold()) if len(token) >= 2}


def _near_duplicate(item: SearchResult, selected: list[SearchResult]) -> bool:
    terms = _term_set(item.content)
    for other in selected:
        if item.content == other.content:
            return True
        if item.path == other.path:
            overlap = max(0, min(item.end_line, other.end_line) - max(item.start_line, other.start_line) + 1)
            smaller = max(1, min(item.end_line - item.start_line + 1, other.end_line - other.start_line + 1))
            if overlap / smaller >= 0.85:
                # Prefer stable/specific identities over generic windows.
                if item.kind == "window" or other.kind != "window":
                    return True
        other_terms = _term_set(other.content)
        union = terms | other_terms
        if union and len(terms & other_terms) / len(union) >= 0.86:
            return True
    return False


def _candidate_file_rows(
    conn: sqlite3.Connection,
    config: ContextConfig,
    query: str,
    tokens: list[str],
    *,
    preferred_paths: set[str],
    excluded_paths: set[str],
) -> list[sqlite3.Row]:
    rows = [row for row in conn.execute("SELECT * FROM files") if str(row["path"]) not in excluded_paths]
    ranked = sorted(
        ((_file_score(row, query, tokens, preferred_paths, config), row) for row in rows),
        key=lambda pair: (-pair[0], str(pair[1]["path"])),
    )
    positives = [row for score, row in ranked if score > 0]
    if not positives:
        positives = [row for _score_value, row in ranked]
    return positives[: config.candidate_files]


def _candidate_chunk_rows(
    conn: sqlite3.Connection,
    paths: list[str],
    tokens: list[str],
    *,
    use_fts: bool,
    limit: int,
) -> list[sqlite3.Row]:
    if not paths:
        return []
    placeholders = ",".join("?" for _ in paths)
    rows: list[sqlite3.Row] = []
    if use_fts and tokens:
        fts_query = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        rows = list(conn.execute(
            f"""
            SELECT c.*, bm25(chunks_fts, 0.0, 2.0, 3.0, 1.0) AS fts_rank
            FROM chunks_fts JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
            WHERE chunks_fts MATCH ? AND c.path IN ({placeholders})
            ORDER BY fts_rank ASC, c.path, c.start_line
            LIMIT ?
            """,
            (fts_query, *paths, max(limit * 4, 200)),
        ))
    if not rows:
        rows = list(conn.execute(
            f"SELECT *, 0.0 AS fts_rank FROM chunks WHERE path IN ({placeholders}) ORDER BY path,start_line,chunk_id",
            tuple(paths),
        ))
        if tokens:
            rows = [row for row in rows if any(
                token in (str(row["path"]) + " " + str(row["label"]) + " " + str(row["selector"] or "") + " " + str(row["content"])).casefold()
                for token in tokens
            )]
    # Keep a few strong chunks from every selected file instead of letting one
    # verbose file consume the complete candidate pool.
    per_file = max(3, math.ceil(limit / max(1, len(paths))))
    counts: dict[str, int] = {}
    balanced: list[sqlite3.Row] = []
    for row in rows:
        path = str(row["path"])
        if counts.get(path, 0) >= per_file:
            continue
        balanced.append(row)
        counts[path] = counts.get(path, 0) + 1
        if len(balanced) >= limit:
            break
    return balanced


def query_context(
    config: ContextConfig,
    query: str,
    *,
    limit: int = 8,
    budget: int | None = None,
    preferred_paths: Iterable[str] = (),
    required_paths: Iterable[str] = (),
    required_identities: Iterable[str] = (),
    excluded_paths: Iterable[str] = (),
) -> SearchReport:
    query = query.strip()
    tokens = _tokens(query)
    if not tokens:
        raise ContextIndexError("context query must contain searchable text")
    if limit < 1 or limit > 50:
        raise ContextIndexError("context query limit must be between 1 and 50")
    budget_value = int(budget if budget is not None else config.default_budget)
    if budget_value < 128:
        raise ContextIndexError("context budget must be at least 128 estimated tokens")
    preferred = {str(item).replace("\\", "/").lstrip("./") for item in preferred_paths}
    required = {str(item).replace("\\", "/").lstrip("./") for item in required_paths}
    preferred |= required
    required_identity_set = {str(item).strip() for item in required_identities if str(item).strip()}
    excluded = {str(item).replace("\\", "/").lstrip("./") for item in excluded_paths} - preferred
    conn = _connect(config.database)
    omitted = {"duplicateOverlap": 0, "sourceKindCap": 0, "contextBudget": 0, "requiredPathBudgetExceeded": 0}
    try:
        meta = {row[0]: row[1] for row in conn.execute("SELECT key, value FROM meta")}
        file_rows = _candidate_file_rows(
            conn, config, query, tokens, preferred_paths=preferred, excluded_paths=excluded
        )
        paths = [str(row["path"]) for row in file_rows]
        # Directly changed files are a hard briefing requirement, not merely a score bonus.
        # Add indexed required paths even when file-first ranking would otherwise cut them off.
        indexed_required = {
            str(row[0]) for row in conn.execute(
                "SELECT path FROM files WHERE path IN (%s)" % ",".join("?" for _ in required),
                tuple(sorted(required)),
            )
        } if required else set()
        for path in sorted(indexed_required):
            if path not in paths:
                paths.append(path)
        candidates = _candidate_chunk_rows(
            conn,
            paths,
            tokens,
            use_fts=meta.get("fts5") == "1",
            limit=max(config.candidate_chunks, limit * 6),
        )
        candidate_ids = {str(row["chunk_id"]) for row in candidates}
        # Stable semantic identities supplied by semantic diff are stronger than
        # lexical ranking. Pull exact current chunks even when file-first/FTS
        # would not otherwise select them. Removed identities naturally have no
        # current chunk and remain visible only in the briefing metadata.
        if required_identity_set:
            placeholders = ",".join("?" for _ in required_identity_set)
            exact_rows = list(conn.execute(
                f"SELECT *, 0.0 AS fts_rank FROM chunks WHERE chunk_id IN ({placeholders}) OR anchor IN ({placeholders}) ORDER BY path,start_line,chunk_id",
                (*sorted(required_identity_set), *sorted(value.removeprefix("semantic:") for value in required_identity_set)),
            ))
            for row in exact_rows:
                chunk_id = str(row["chunk_id"])
                if chunk_id not in candidate_ids:
                    candidates.append(row)
                    candidate_ids.add(chunk_id)
        # FTS can legitimately return no query-matching chunk from a required file. Pull a
        # small direct pool so at least one semantic/window representation can be reserved.
        for path in sorted(indexed_required):
            if any(str(row["path"]) == path for row in candidates):
                continue
            direct = list(conn.execute(
                "SELECT *, 0.0 AS fts_rank FROM chunks WHERE path=? ORDER BY CASE WHEN kind='window' THEN 1 ELSE 0 END,start_line,chunk_id LIMIT 8",
                (path,),
            ))
            for row in direct:
                if str(row["chunk_id"]) not in candidate_ids:
                    candidates.append(row)
                    candidate_ids.add(str(row["chunk_id"]))
        ranked: list[SearchResult] = []
        for index, row in enumerate(candidates):
            score, why = _score(
                row, query, tokens, max(1.0, len(candidates) - index), preferred=preferred, config=config
            )
            ranked.append(SearchResult(
                chunk_id=str(row["chunk_id"]), path=str(row["path"]), kind=str(row["kind"]), label=str(row["label"]),
                anchor=str(row["anchor"]) if row["anchor"] is not None else None,
                selector=str(row["selector"]) if row["selector"] is not None else None,
                start_line=int(row["start_line"]), end_line=int(row["end_line"]), content=str(row["content"]),
                source_kind=str(row["source_kind"]), weight=float(row["weight"]), priority=str(row["priority"]),
                score=score, why=why,
            ))
        ranked.sort(key=lambda item: (-item.score, item.path, item.start_line, item.chunk_id))
        # Reserve exact changed semantic identities first, then one best chunk for
        # each directly changed path. This preserves the existing file safety net
        # while letting semantic diff narrow resume/affected context precisely.
        semantic_required: list[SearchResult] = [
            item for item in ranked
            if item.chunk_id in required_identity_set or (item.anchor and item.anchor in required_identity_set)
        ]
        semantic_required.sort(key=lambda item: (item.path, item.start_line, item.chunk_id))
        semantic_required_ids = {item.chunk_id for item in semantic_required}
        required_best: list[SearchResult] = []
        for path in sorted(indexed_required):
            items = [item for item in ranked if item.path == path and item.chunk_id not in semantic_required_ids]
            if items:
                required_best.append(items[0])
        required_ids = semantic_required_ids | {item.chunk_id for item in required_best}
        ranked = [*semantic_required, *required_best, *(item for item in ranked if item.chunk_id not in required_ids)]
        # Give each relevant normal-priority source kind one early opportunity. This
        # prevents implementation/tests from filling a small result-count limit before
        # a matching architecture/schema chunk can be considered.
        first_by_kind: dict[str, SearchResult] = {}
        for item in ranked:
            if item.priority != "normal":
                continue
            first_by_kind.setdefault(item.source_kind, item)
        diversity = sorted(first_by_kind.values(), key=lambda item: (-item.score, item.path, item.start_line, item.chunk_id))
        diversity_ids = {item.chunk_id for item in diversity}
        non_required_diversity = [item for item in diversity if item.chunk_id not in required_ids]
        ranked = [*semantic_required, *required_best, *non_required_diversity, *(item for item in ranked if item.chunk_id not in diversity_ids and item.chunk_id not in required_ids)]

        selected: list[SearchResult] = []
        used_tokens = 0
        kind_tokens: dict[str, int] = {}
        seen_hashes: set[str] = set()
        for item in ranked:
            digest = hashlib.sha256(item.content.encode("utf-8")).hexdigest()
            if digest in seen_hashes or _near_duplicate(item, selected):
                omitted["duplicateOverlap"] += 1
                continue
            item_tokens = _estimate_tokens(item.content)
            remaining = budget_value - used_tokens
            is_required = item.chunk_id in required_ids
            if remaining <= 0:
                omitted["requiredPathBudgetExceeded" if is_required else "contextBudget"] += 1
                continue
            cap = None if is_required else config.kind_caps.get(item.source_kind)
            kind_remaining = remaining
            if cap is not None:
                kind_remaining = min(kind_remaining, max(0, int(budget_value * cap) - kind_tokens.get(item.source_kind, 0)))
                if kind_remaining <= 0:
                    omitted["sourceKindCap"] += 1
                    continue
            allowed_tokens = min(remaining, kind_remaining)
            content = item.content
            if item_tokens > allowed_tokens:
                max_chars = max(32, allowed_tokens * 4 - 20)
                content = content[:max_chars].rstrip() + "\n… truncated\n"
                item_tokens = _estimate_tokens(content)
                if item_tokens > allowed_tokens:
                    omitted["sourceKindCap" if kind_remaining < remaining else "contextBudget"] += 1
                    continue
            if not content.strip():
                continue
            selected.append(SearchResult(**{**item.__dict__, "content": content}))
            seen_hashes.add(digest)
            used_tokens += item_tokens
            kind_tokens[item.source_kind] = kind_tokens.get(item.source_kind, 0) + item_tokens
            if len(selected) >= limit:
                break
        if len(ranked) > len(selected):
            omitted["contextBudget"] += max(0, len(ranked) - len(selected) - omitted["duplicateOverlap"] - omitted["sourceKindCap"])
        return SearchReport(
            query=query,
            results=tuple(selected),
            candidate_files=len(file_rows),
            candidate_chunks=len(candidates),
            estimated_tokens=used_tokens,
            budget=budget_value,
            omitted={k: v for k, v in omitted.items() if v},
        )
    finally:
        conn.close()


def query_index(
    config: ContextConfig,
    query: str,
    *,
    limit: int = 8,
    max_chars: int = 12000,
) -> list[SearchResult]:
    # Backward-compatible facade. Stage C3 uses an estimated 4 chars/token budget.
    return list(query_context(config, query, limit=limit, budget=max(128, math.ceil(max_chars / 4))).results)


def inspect_identity(config: ContextConfig, identity: str) -> SearchResult | None:
    normalized = identity.strip()
    conn = _connect(config.database)
    try:
        row = conn.execute(
            "SELECT * FROM chunks WHERE chunk_id=? OR anchor=? ORDER BY CASE WHEN chunk_id=? THEN 0 ELSE 1 END LIMIT 1",
            (normalized, normalized.removeprefix("semantic:"), normalized),
        ).fetchone()
        if row is None:
            return None
        return SearchResult(
            chunk_id=str(row["chunk_id"]), path=str(row["path"]), kind=str(row["kind"]), label=str(row["label"]),
            anchor=str(row["anchor"]) if row["anchor"] is not None else None,
            selector=str(row["selector"]) if row["selector"] is not None else None,
            start_line=int(row["start_line"]), end_line=int(row["end_line"]), content=str(row["content"]),
            source_kind=str(row["source_kind"]), weight=float(row["weight"]), priority=str(row["priority"]),
            score=100.0 * float(row["weight"]), why=("exact stable identity",),
        )
    finally:
        conn.close()


def inspect_anchor(config: ContextConfig, anchor: str) -> SearchResult | None:
    return inspect_identity(config, anchor)
