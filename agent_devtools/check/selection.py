from __future__ import annotations

from dataclasses import dataclass

from agent_devtools.core.pathmatch import matches_pattern

from .config import CheckConfig
from .policy import PolicyError, SelectionPlan


@dataclass(frozen=True)
class SelectionContext:
    application_suite_id: str = "application"


def _force_selected_suite(plan: SelectionPlan, suite_id: str, reason: str, suite_order: tuple[str, ...]) -> None:
    if suite_id not in suite_order:
        raise PolicyError(f"Safety guard references unsupported suite: {suite_id}")
    selected = list(plan.selected)
    if any(item.get("id") == suite_id for item in selected):
        return
    skipped = [item for item in plan.skipped if item.get("id") != suite_id]
    selected.append({"id": suite_id, "reason": reason, "hit": []})
    order = {name: index for index, name in enumerate(suite_order)}
    selected.sort(key=lambda item: order.get(str(item.get("id")), len(order)))
    plan.selected = tuple(selected)
    plan.skipped = tuple(skipped)


def _force_all_application_groups(plan: SelectionPlan, reason: str) -> None:
    combined = list(plan.selected_application_groups) + list(plan.skipped_application_groups)
    by_id = {str(item.get("id")): dict(item) for item in combined}
    plan.selected_application_groups = tuple(
        {"id": group_id, "reason": reason, "hit": list((by_id.get(group_id) or {}).get("hit") or [])}
        for group_id in plan.application_group_order
    )
    plan.skipped_application_groups = ()


def apply_selection_safety_guards(
    plan: SelectionPlan,
    config: CheckConfig,
    *,
    context: SelectionContext = SelectionContext(),
) -> SelectionPlan:
    if plan.fallback_full:
        plan.selected = tuple(
            {"id": suite_id, "reason": "fail-safe full selection", "hit": []}
            for suite_id in config.suite_order
        )
        plan.skipped = ()
        _force_all_application_groups(plan, "fail-safe full selection")
        return plan

    for rel in plan.changed_files:
        for guard in config.guards:
            if any(matches_pattern(rel, pattern) for pattern in guard.patterns):
                reason = f"safety guard: runner/test source changed ({rel})"
                _force_selected_suite(plan, guard.suite, reason, config.suite_order)
                if guard.select_all_application_groups:
                    _force_all_application_groups(plan, reason)

    selected_ids = {str(item.get("id")) for item in plan.selected}
    if context.application_suite_id in selected_ids and not plan.selected_application_groups and plan.application_group_order:
        _force_all_application_groups(plan, "safety guard: application selected but no semantic group matched")

    for dependency in config.dependencies:
        selected_ids = {str(item.get("id")) for item in plan.selected}
        if selected_ids.intersection(dependency.if_selected_any):
            _force_selected_suite(plan, dependency.require, dependency.reason, config.suite_order)
    return plan


def render_selection_plan(plan: SelectionPlan, *, base: str | None = None, explain: bool = False) -> str:
    selected = [str(item.get("id")) for item in plan.selected]
    skipped = [str(item.get("id")) for item in plan.skipped]
    lines = [
        f"PLAN {plan.profile} · base={base or 'HEAD'} · changed={len(plan.changed_files)} · selected={len(selected)} · skipped={len(skipped)}",
        "impact initial: " + (", ".join(plan.initial_impact) or "none"),
        "impact effective: " + (", ".join(plan.effective_impact) or "none"),
        f"mask: {hex(plan.initial_mask)} -> {hex(plan.effective_mask)}",
    ]
    if plan.fallback_full:
        lines.append("FALLBACK FULL: " + "; ".join(plan.fallback_reasons))
    if explain:
        if plan.changed_files:
            lines.append("changed files:")
            lines.extend(f"  {path}" for path in plan.changed_files)
        if plan.propagated:
            lines.append("propagation:")
            lines.extend(f"  {edge['from']} -> {edge['to']}" for edge in plan.propagated)
        lines.append("selected suites:")
        for item in plan.selected:
            hit = ",".join(item.get("hit") or []) or "-"
            lines.append(f"  SELECT {item.get('id')} · hit={hit} · {item.get('reason')}")
        if plan.skipped:
            lines.append("skipped suites:")
            for item in plan.skipped:
                lines.append(f"  SKIP {item.get('id')} · {item.get('reason')}")
        if plan.application_group_order:
            lines.append("application groups:")
            for item in plan.selected_application_groups:
                hit = ",".join(item.get("hit") or []) or "-"
                lines.append(f"  SELECT-GROUP {item.get('id')} · hit={hit} · {item.get('reason')}")
            for item in plan.skipped_application_groups:
                lines.append(f"  SKIP-GROUP {item.get('id')} · {item.get('reason')}")
    else:
        lines.append("select: " + (", ".join(selected) or "none"))
        if skipped:
            lines.append("skip: " + ", ".join(skipped))
        if plan.selected_application_groups or plan.skipped_application_groups:
            lines.append(
                f"application-groups: {len(plan.selected_application_groups)} selected / {len(plan.skipped_application_groups)} skipped"
            )
    return "\n".join(lines)
