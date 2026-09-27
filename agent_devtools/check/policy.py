from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

GENERIC_POLICY_FORMAT = "agent-devtools-check-policy"
GENERIC_POLICY_VERSION = 1
LEGACY_ORGANIZER_POLICY_FORMAT = "business-organizer-next-agent-check-policy"
LEGACY_ORGANIZER_POLICY_VERSION = 2
SUPPORTED_POLICY_FORMATS = {
    (GENERIC_POLICY_FORMAT, GENERIC_POLICY_VERSION),
    (LEGACY_ORGANIZER_POLICY_FORMAT, LEGACY_ORGANIZER_POLICY_VERSION),
}
RESERVED_GLOBAL = "global"
RESERVED_UNKNOWN = "unknown"
RESERVED_FEATURES = {RESERVED_GLOBAL, RESERVED_UNKNOWN}
FEATURE_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")


class PolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceRule:
    patterns: tuple[str, ...]
    impact: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True)
class SuitePolicy:
    suite_id: str
    impact: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True)
class ApplicationGroupPolicy:
    group_id: str
    impact: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True)
class ProfilePolicy:
    profile_id: str
    selection: str
    suites: tuple[str, ...]
    always: tuple[str, ...]


@dataclass(frozen=True)
class ReusableStagePolicy:
    stage_id: str
    tool: str
    inputs: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True)
class CertificationPolicy:
    force_cold_patterns: tuple[str, ...]
    application_global_inputs: tuple[str, ...]
    reusable_stages: dict[str, ReusableStagePolicy]


@dataclass
class SelectionPlan:
    profile: str
    changed_files: tuple[str, ...]
    initial_mask: int
    effective_mask: int
    initial_impact: tuple[str, ...]
    effective_impact: tuple[str, ...]
    propagated: tuple[dict[str, str], ...]
    selected: tuple[dict[str, Any], ...]
    skipped: tuple[dict[str, Any], ...]
    selected_application_groups: tuple[dict[str, Any], ...]
    skipped_application_groups: tuple[dict[str, Any], ...]
    application_group_order: tuple[str, ...]
    fallback_full: bool
    fallback_reasons: tuple[str, ...]
    feature_bits: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "changedFiles": list(self.changed_files),
            "initialImpact": list(self.initial_impact),
            "effectiveImpact": list(self.effective_impact),
            "initialMaskHex": hex(self.initial_mask),
            "effectiveMaskHex": hex(self.effective_mask),
            "featureBits": {name: hex(bit) for name, bit in self.feature_bits.items()},
            "propagated": list(self.propagated),
            "selectedSuites": list(self.selected),
            "skippedSuites": list(self.skipped),
            "selectedApplicationGroups": list(self.selected_application_groups),
            "skippedApplicationGroups": list(self.skipped_application_groups),
            "applicationGroupOrder": list(self.application_group_order),
            "fallbackFull": self.fallback_full,
            "fallbackReasons": list(self.fallback_reasons),
        }


class CheckPolicy:
    def __init__(
        self,
        *,
        path: Path,
        features: dict[str, tuple[str, ...]],
        sources: tuple[SourceRule, ...],
        suites: dict[str, SuitePolicy],
        application_groups: dict[str, ApplicationGroupPolicy],
        profiles: dict[str, ProfilePolicy],
        certification: CertificationPolicy | None,
        source_format: str,
        source_format_version: int,
    ) -> None:
        self.path = path
        self.features = features
        self.sources = sources
        self.suites = suites
        self.application_groups = application_groups
        self.profiles = profiles
        self.certification = certification
        self.source_format = source_format
        self.source_format_version = source_format_version
        names = sorted(set(features) | RESERVED_FEATURES)
        self.feature_bits = {name: 1 << index for index, name in enumerate(names)}
        self.all_feature_mask = 0
        for name, bit in self.feature_bits.items():
            if name not in RESERVED_FEATURES:
                self.all_feature_mask |= bit

    @property
    def global_bit(self) -> int:
        return self.feature_bits[RESERVED_GLOBAL]

    @property
    def unknown_bit(self) -> int:
        return self.feature_bits[RESERVED_UNKNOWN]

    def mask_for(self, names: Iterable[str]) -> int:
        value = 0
        for name in names:
            try:
                value |= self.feature_bits[name]
            except KeyError as exc:
                raise PolicyError(f"Unknown impact feature: {name}") from exc
        return value

    def names_for_mask(self, mask: int, *, include_reserved: bool = True) -> tuple[str, ...]:
        names = [name for name, bit in self.feature_bits.items() if mask & bit]
        if not include_reserved:
            names = [name for name in names if name not in RESERVED_FEATURES]
        return tuple(sorted(names))

    def classify(self, changed_files: Iterable[str]) -> tuple[int, dict[str, tuple[str, ...]]]:
        initial = 0
        by_file: dict[str, tuple[str, ...]] = {}
        for raw in changed_files:
            rel = str(raw).replace("\\", "/").lstrip("./")
            names: set[str] = set()
            for rule in self.sources:
                if any(fnmatch.fnmatchcase(rel, pattern) for pattern in rule.patterns):
                    names.update(rule.impact)
            if not names:
                names.add(RESERVED_UNKNOWN)
            by_file[rel] = tuple(sorted(names))
            initial |= self.mask_for(names)
        return initial, by_file

    def expand(self, initial_mask: int) -> tuple[int, tuple[dict[str, str], ...]]:
        if initial_mask & (self.global_bit | self.unknown_bit):
            return initial_mask | self.all_feature_mask, ()
        effective = initial_mask
        propagated: list[dict[str, str]] = []
        changed = True
        while changed:
            changed = False
            for source in sorted(self.features):
                source_bit = self.feature_bits[source]
                if not (effective & source_bit):
                    continue
                for target in self.features[source]:
                    target_bit = self.feature_bits[target]
                    if effective & target_bit:
                        continue
                    effective |= target_bit
                    propagated.append({"from": source, "to": target})
                    changed = True
        return effective, tuple(propagated)

    def plan(self, profile_id: str, changed_files: Iterable[str]) -> SelectionPlan:
        if profile_id not in self.profiles:
            raise PolicyError(f"Unknown policy profile: {profile_id}")
        profile = self.profiles[profile_id]
        changed = tuple(sorted({str(item).replace("\\", "/") for item in changed_files if str(item).strip()}))
        initial_mask, _by_file = self.classify(changed)
        effective_mask, propagated = self.expand(initial_mask)
        fallback_reasons: list[str] = []
        if initial_mask & self.unknown_bit:
            fallback_reasons.append("unknown source classification")
        if initial_mask & self.global_bit:
            fallback_reasons.append("global-impact source changed")
        fallback_full = bool(fallback_reasons)

        selected: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        always = set(profile.always)
        for suite_id in profile.suites:
            suite = self.suites[suite_id]
            suite_mask = self.mask_for(suite.impact)
            hits = self.names_for_mask(effective_mask & suite_mask, include_reserved=False)
            if profile.selection == "all" or fallback_full or suite_id in always or hits:
                if fallback_full:
                    reason = "fail-safe full selection"
                elif profile.selection == "all":
                    reason = "profile selects all suites"
                elif suite_id in always:
                    reason = "profile always runs this suite"
                else:
                    reason = "impact intersection"
                selected.append({"id": suite_id, "reason": reason, "hit": list(hits)})
            else:
                skipped.append({"id": suite_id, "reason": "no impact intersection", "hit": []})

        selected_suite_ids = {str(item["id"]) for item in selected}
        selected_groups: list[dict[str, Any]] = []
        skipped_groups: list[dict[str, Any]] = []
        application_selected = "application" in selected_suite_ids
        for group_id, group in self.application_groups.items():
            group_mask = self.mask_for(group.impact)
            hits = self.names_for_mask(effective_mask & group_mask, include_reserved=False)
            if not application_selected:
                skipped_groups.append({"id": group_id, "reason": "application suite not selected", "hit": []})
            elif profile.selection == "all" or fallback_full:
                selected_groups.append({
                    "id": group_id,
                    "reason": "fail-safe full selection" if fallback_full else "profile selects all application groups",
                    "hit": list(hits),
                })
            elif hits:
                selected_groups.append({"id": group_id, "reason": "impact intersection", "hit": list(hits)})
            else:
                skipped_groups.append({"id": group_id, "reason": "no impact intersection", "hit": []})

        return SelectionPlan(
            profile=profile_id,
            changed_files=changed,
            initial_mask=initial_mask,
            effective_mask=effective_mask,
            initial_impact=self.names_for_mask(initial_mask),
            effective_impact=self.names_for_mask(effective_mask),
            propagated=propagated,
            selected=tuple(selected),
            skipped=tuple(skipped),
            selected_application_groups=tuple(selected_groups),
            skipped_application_groups=tuple(skipped_groups),
            application_group_order=tuple(self.application_groups),
            fallback_full=fallback_full,
            fallback_reasons=tuple(fallback_reasons),
            feature_bits=dict(sorted(self.feature_bits.items())),
        )


def _string_list(value: Any, *, field: str, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise PolicyError(f"{field} must be {'a non-empty' if not allow_empty else 'an'} array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise PolicyError(f"{field} must contain non-empty strings")
        result.append(item.strip())
    if len(set(result)) != len(result):
        raise PolicyError(f"{field} contains duplicates")
    return tuple(result)


def load_policy(path: Path) -> CheckPolicy:
    policy_path = path.resolve()
    try:
        raw = json.loads(policy_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PolicyError(f"Policy file not found: {policy_path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"Policy file is unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyError("Policy root must be an object")
    fmt = str(raw.get("format") or "")
    version = int(raw.get("formatVersion") or 0)
    if (fmt, version) not in SUPPORTED_POLICY_FORMATS:
        raise PolicyError(f"Unsupported check policy format/version: {fmt!r} v{version}")

    raw_features = raw.get("features")
    if not isinstance(raw_features, dict) or not raw_features:
        raise PolicyError("features must be a non-empty object")
    features: dict[str, tuple[str, ...]] = {}
    for feature, spec in raw_features.items():
        if not isinstance(feature, str) or not FEATURE_ID_RE.match(feature) or feature in RESERVED_FEATURES:
            raise PolicyError(f"Invalid or reserved feature id: {feature!r}")
        if not isinstance(spec, dict):
            raise PolicyError(f"Feature {feature!r} must be an object")
        features[feature] = _string_list(spec.get("affects", []), field=f"features.{feature}.affects")
    for feature, targets in features.items():
        for target in targets:
            if target not in features:
                raise PolicyError(f"features.{feature}.affects references unknown feature {target!r}")

    valid_impacts = set(features) | RESERVED_FEATURES
    raw_sources = raw.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise PolicyError("sources must be a non-empty array")
    sources: list[SourceRule] = []
    for index, spec in enumerate(raw_sources):
        if not isinstance(spec, dict):
            raise PolicyError(f"sources[{index}] must be an object")
        patterns = _string_list(spec.get("patterns"), field=f"sources[{index}].patterns", allow_empty=False)
        impact = _string_list(spec.get("impact"), field=f"sources[{index}].impact", allow_empty=False)
        unknown = sorted(set(impact) - valid_impacts)
        if unknown:
            raise PolicyError(f"sources[{index}].impact references unknown feature(s): {', '.join(unknown)}")
        sources.append(SourceRule(patterns=patterns, impact=impact, description=str(spec.get("description") or "")))

    raw_suites = raw.get("suites")
    if not isinstance(raw_suites, dict) or not raw_suites:
        raise PolicyError("suites must be a non-empty object")
    suites: dict[str, SuitePolicy] = {}
    for suite_id, spec in raw_suites.items():
        if not isinstance(suite_id, str) or not FEATURE_ID_RE.match(suite_id):
            raise PolicyError(f"Invalid suite id: {suite_id!r}")
        if not isinstance(spec, dict):
            raise PolicyError(f"Suite {suite_id!r} must be an object")
        impact = _string_list(spec.get("impact", []), field=f"suites.{suite_id}.impact")
        unknown = sorted(set(impact) - valid_impacts)
        if unknown:
            raise PolicyError(f"suites.{suite_id}.impact references unknown feature(s): {', '.join(unknown)}")
        suites[suite_id] = SuitePolicy(suite_id=suite_id, impact=impact, description=str(spec.get("description") or ""))

    raw_application_groups = raw.get("applicationGroups", {})
    if not isinstance(raw_application_groups, dict):
        raise PolicyError("applicationGroups must be an object")
    application_groups: dict[str, ApplicationGroupPolicy] = {}
    for group_id, spec in raw_application_groups.items():
        if not isinstance(group_id, str) or not FEATURE_ID_RE.match(group_id):
            raise PolicyError(f"Invalid application group id: {group_id!r}")
        if not isinstance(spec, dict):
            raise PolicyError(f"Application group {group_id!r} must be an object")
        impact = _string_list(spec.get("impact"), field=f"applicationGroups.{group_id}.impact", allow_empty=False)
        unknown = sorted(set(impact) - valid_impacts)
        if unknown:
            raise PolicyError(f"applicationGroups.{group_id}.impact references unknown feature(s): {', '.join(unknown)}")
        application_groups[group_id] = ApplicationGroupPolicy(
            group_id=group_id,
            impact=impact,
            description=str(spec.get("description") or ""),
        )

    certification: CertificationPolicy | None = None
    raw_certification = raw.get("certification")
    if raw_certification is not None:
        if not isinstance(raw_certification, dict):
            raise PolicyError("certification must be an object")
        force_cold_patterns = _string_list(
            raw_certification.get("forceColdPatterns", []),
            field="certification.forceColdPatterns",
        )
        application_global_inputs = _string_list(
            raw_certification.get("applicationGlobalInputs", []),
            field="certification.applicationGlobalInputs",
        )
        raw_reusable_stages = raw_certification.get("reusableStages", {})
        if not isinstance(raw_reusable_stages, dict):
            raise PolicyError("certification.reusableStages must be an object")
        reusable_stages: dict[str, ReusableStagePolicy] = {}
        for stage_id, spec in raw_reusable_stages.items():
            if not isinstance(stage_id, str) or not FEATURE_ID_RE.match(stage_id):
                raise PolicyError(f"Invalid reusable certification stage id: {stage_id!r}")
            if not isinstance(spec, dict):
                raise PolicyError(f"certification.reusableStages.{stage_id} must be an object")
            tool = str(spec.get("tool") or "").strip()
            if not tool:
                raise PolicyError(f"certification.reusableStages.{stage_id}.tool is required")
            inputs = _string_list(spec.get("inputs"), field=f"certification.reusableStages.{stage_id}.inputs", allow_empty=False)
            reusable_stages[stage_id] = ReusableStagePolicy(
                stage_id=stage_id,
                tool=tool,
                inputs=inputs,
                description=str(spec.get("description") or ""),
            )
        certification = CertificationPolicy(
            force_cold_patterns=force_cold_patterns,
            application_global_inputs=application_global_inputs,
            reusable_stages=reusable_stages,
        )

    raw_profiles = raw.get("profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise PolicyError("profiles must be a non-empty object")
    profiles: dict[str, ProfilePolicy] = {}
    for profile_id, spec in raw_profiles.items():
        if not isinstance(profile_id, str) or not FEATURE_ID_RE.match(profile_id):
            raise PolicyError(f"Invalid profile id: {profile_id!r}")
        if not isinstance(spec, dict):
            raise PolicyError(f"Profile {profile_id!r} must be an object")
        selection = str(spec.get("selection") or "")
        if selection not in {"affected", "all"}:
            raise PolicyError(f"profiles.{profile_id}.selection must be 'affected' or 'all'")
        profile_suites = _string_list(spec.get("suites"), field=f"profiles.{profile_id}.suites", allow_empty=False)
        always = _string_list(spec.get("always", []), field=f"profiles.{profile_id}.always")
        unknown_suites = sorted((set(profile_suites) | set(always)) - set(suites))
        if unknown_suites:
            raise PolicyError(f"profiles.{profile_id} references unknown suite(s): {', '.join(unknown_suites)}")
        if not set(always).issubset(set(profile_suites)):
            raise PolicyError(f"profiles.{profile_id}.always must be a subset of suites")
        profiles[profile_id] = ProfilePolicy(profile_id=profile_id, selection=selection, suites=profile_suites, always=always)

    return CheckPolicy(
        path=policy_path,
        features=features,
        sources=tuple(sources),
        suites=suites,
        application_groups=application_groups,
        profiles=profiles,
        certification=certification,
        source_format=fmt,
        source_format_version=version,
    )
