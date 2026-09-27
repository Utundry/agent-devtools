from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_devtools.check.changes import git_changed_files
from agent_devtools.check.config import CheckConfigError, load_check_config
from agent_devtools.check.policy import PolicyError, RESERVED_GLOBAL, RESERVED_UNKNOWN, load_policy
from agent_devtools.check.selection import apply_selection_safety_guards

from .config import ContextConfig
from .index import ContextIndexError, _connect, ensure_index, related_paths
from .search import SearchReport, SearchResult, query_context


@dataclass(frozen=True)
class AffectedBriefing:
    changed_files: tuple[str, ...]
    impact_available: bool
    initial_impact: tuple[str, ...]
    effective_impact: tuple[str, ...]
    selected_suites: tuple[str, ...]
    selected_application_groups: tuple[str, ...]
    fallback_full: bool
    fallback_reasons: tuple[str, ...]
    query: str
    results: tuple[SearchResult, ...]
    candidate_files: int
    candidate_chunks: int
    estimated_tokens: int
    budget: int
    omitted: dict[str, int]
    required_paths: tuple[str, ...]
    preferred_paths: tuple[str, ...]
    excluded_paths: tuple[str, ...]
    semantic_identities: tuple[str, ...] = ()

    def scope_payload(self) -> dict[str, object]:
        """Lossless public projection of affected-scope facts.

        Keep the CLI/API surface derived from the briefing object so newly added
        scope semantics cannot silently remain internal-only.
        """
        return {
            "requiredPaths": list(self.required_paths),
            "preferredPaths": list(self.preferred_paths),
            "excludedPaths": list(self.excluded_paths),
            "requiredPathBudgetExceeded": int(self.omitted.get("requiredPathBudgetExceeded", 0)),
        }


def resolve_changed(root: Path, explicit: list[str] | tuple[str, ...] | None, base: str | None) -> tuple[str, ...]:
    if explicit:
        return tuple(sorted({str(item).replace("\\", "/").lstrip("./") for item in explicit if str(item).strip()}))
    discovered = git_changed_files(root, base)
    if discovered is None:
        raise ContextIndexError("cannot discover changed files; provide --changed when Git/base discovery is unavailable")
    return tuple(sorted(discovered))


def _path_terms(paths: tuple[str, ...]) -> list[str]:
    stop = {"src", "source", "app", "lib", "backend", "frontend", "tests", "test", "docs", "doc"}
    terms: list[str] = []
    for raw in paths:
        path = Path(raw)
        for part in (*path.parts[-3:-1], path.stem):
            token = str(part).strip().casefold().replace("_", "-")
            if len(token) >= 3 and token not in stop and token not in terms:
                terms.append(token)
    return terms


def _context_scope(context_config: ContextConfig, policy, effective: tuple[str, ...], changed: tuple[str, ...], fallback_full: bool) -> tuple[set[str], set[str], set[str]]:
    conn = _connect(context_config.database)
    try:
        indexed = [(str(row["path"]), str(row["source_kind"])) for row in conn.execute("SELECT path, source_kind FROM files")]
    finally:
        conn.close()
    indexed_paths = {path for path, _kind in indexed}
    required: set[str] = set(changed) & indexed_paths
    if fallback_full:
        return required, set(required), set()
    effective_set = set(effective) - {RESERVED_UNKNOWN, RESERVED_GLOBAL}
    preferred: set[str] = set()
    excluded: set[str] = set()
    knowledge_kinds = {"documentation", "architecture", "schema", "history", "release"}
    for path, source_kind in indexed:
        if source_kind in knowledge_kinds:
            continue
        _mask, by_file = policy.classify((path,))
        names = set(by_file.get(path, ()))
        known = names - {RESERVED_UNKNOWN, RESERVED_GLOBAL}
        if not known:
            continue
        if not (known & effective_set):
            excluded.add(path)
    expanded = related_paths(context_config, required, depth=context_config.max_graph_depth)
    preferred.update(expanded - required)
    excluded -= required
    excluded -= preferred
    return required, preferred, excluded


def build_affected_briefing(
    root: Path,
    context_config: ContextConfig,
    *,
    changed_files: tuple[str, ...],
    profile: str = "affected",
    limit: int = 10,
    max_chars: int | None = None,
    budget: int | None = None,
    semantic_identities: tuple[str, ...] = (),
) -> AffectedBriefing:
    ensure_index(context_config)
    initial: tuple[str, ...] = ()
    effective: tuple[str, ...] = ()
    suites: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    fallback_full = False
    fallback_reasons: tuple[str, ...] = ()
    impact_available = False
    required: set[str] = set()
    preferred: set[str] = set()
    excluded: set[str] = set()

    try:
        check_config = load_check_config(root)
        policy = load_policy(check_config.policy_path)
        plan = apply_selection_safety_guards(policy.plan(profile, changed_files), check_config)
        initial = tuple(plan.initial_impact)
        effective = tuple(plan.effective_impact)
        suites = tuple(str(item.get("id")) for item in plan.selected)
        groups = tuple(str(item.get("id")) for item in plan.selected_application_groups)
        fallback_full = bool(plan.fallback_full)
        fallback_reasons = tuple(plan.fallback_reasons)
        impact_available = True
        required, preferred, excluded = _context_scope(context_config, policy, effective, changed_files, fallback_full)
    except (CheckConfigError, PolicyError, OSError):
        pass

    semantic_identities = tuple(dict.fromkeys(str(item).strip() for item in semantic_identities if str(item).strip()))
    terms: list[str] = []
    for value in (*semantic_identities, *effective, *groups, *_path_terms(changed_files)):
        normalized = str(value).strip().replace("_", "-")
        if normalized and normalized not in terms and normalized not in {RESERVED_UNKNOWN, RESERVED_GLOBAL}:
            terms.append(normalized)
    if not terms:
        terms.extend(_path_terms(changed_files))
    if not terms:
        terms.append("project")
    query = " ".join(terms[:32])
    budget_value = budget
    if budget_value is None and max_chars is not None:
        budget_value = max(128, (max_chars + 3) // 4)
    report: SearchReport = query_context(
        context_config,
        query,
        limit=limit,
        budget=budget_value,
        preferred_paths=preferred,
        required_paths=required,
        required_identities=semantic_identities,
        excluded_paths=excluded,
    )
    return AffectedBriefing(
        changed_files=changed_files,
        semantic_identities=semantic_identities,
        impact_available=impact_available,
        initial_impact=initial,
        effective_impact=effective,
        selected_suites=suites,
        selected_application_groups=groups,
        fallback_full=fallback_full,
        fallback_reasons=fallback_reasons,
        query=query,
        results=report.results,
        candidate_files=report.candidate_files,
        candidate_chunks=report.candidate_chunks,
        estimated_tokens=report.estimated_tokens,
        budget=report.budget,
        omitted=report.omitted,
        required_paths=tuple(sorted(required)),
        preferred_paths=tuple(sorted(preferred)),
        excluded_paths=tuple(sorted(excluded)),
    )
