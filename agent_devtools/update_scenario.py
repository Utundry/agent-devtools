from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FORMAT = "agent-devtools-update-scenario"
FORMAT_VERSION = 1
_SAFE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9._-]+)?$")


class UpdateScenarioError(RuntimeError):
    pass


@dataclass(frozen=True)
class UpdateScenario:
    path: Path
    digest: str
    from_version: str
    to_version: str
    title: str
    commit_message: str
    base_blobs: dict[str, str]
    changes: tuple[dict[str, Any], ...]


def _safe_rel(value: Any) -> str:
    rel = str(value or "").replace("\\", "/").strip()
    if not rel or rel.startswith("/") or rel == ".." or rel.startswith("../") or "/../" in f"/{rel}/":
        raise UpdateScenarioError(f"unsafe scenario path: {rel!r}")
    return rel


def _path(root: Path, rel: str) -> Path:
    root = root.resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise UpdateScenarioError(f"scenario path escapes project root: {rel}") from exc
    return target


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def load_scenario(path: Path) -> UpdateScenario:
    path = path.expanduser().resolve()
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateScenarioError(f"cannot read update scenario: {exc}") from exc
    if not isinstance(payload, dict):
        raise UpdateScenarioError("update scenario must be a JSON object")
    if payload.get("format") != FORMAT or payload.get("formatVersion") != FORMAT_VERSION:
        raise UpdateScenarioError("unsupported update scenario format/version")

    from_version = str(payload.get("fromVersion") or "").strip()
    to_version = str(payload.get("toVersion") or "").strip()
    if not _SAFE_VERSION.fullmatch(from_version) or not _SAFE_VERSION.fullmatch(to_version):
        raise UpdateScenarioError("scenario fromVersion/toVersion must be semantic release versions")
    if from_version == to_version:
        raise UpdateScenarioError("scenario fromVersion and toVersion must differ")

    title = str(payload.get("title") or "").strip()
    commit_message = str(payload.get("commitMessage") or title or f"Apply update scenario {to_version}").strip()
    if not commit_message or "\n" in commit_message or "\r" in commit_message:
        raise UpdateScenarioError("scenario commitMessage must be one non-empty line")

    base_raw = payload.get("baseBlobs", {})
    if not isinstance(base_raw, dict):
        raise UpdateScenarioError("scenario baseBlobs must be an object")
    base_blobs: dict[str, str] = {}
    for key, value in base_raw.items():
        rel = _safe_rel(key)
        digest = str(value or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", digest):
            raise UpdateScenarioError(f"invalid Git blob SHA-1 for {rel}")
        base_blobs[rel] = digest

    changes_raw = payload.get("changes")
    if not isinstance(changes_raw, list) or not changes_raw:
        raise UpdateScenarioError("scenario changes must be a non-empty array")
    changes: list[dict[str, Any]] = []
    for index, raw_change in enumerate(changes_raw):
        if not isinstance(raw_change, dict):
            raise UpdateScenarioError(f"scenario change {index} must be an object")
        change = dict(raw_change)
        op = str(change.get("op") or "")
        if op not in {"replace", "write", "delete", "assert_contains"}:
            raise UpdateScenarioError(f"unsupported scenario op at index {index}: {op!r}")
        change["path"] = _safe_rel(change.get("path"))

        if op == "replace":
            before, after = change.get("before"), change.get("after")
            if not isinstance(before, str) or not before or not isinstance(after, str) or not after:
                raise UpdateScenarioError(f"replace change {index} requires non-empty string before/after")
            if before == after:
                raise UpdateScenarioError(f"replace change {index} before/after must differ")
        elif op == "write":
            if not isinstance(change.get("content"), str):
                raise UpdateScenarioError(f"write change {index} requires string content")
            expected = change.get("expectedSha256")
            if expected is not None and not re.fullmatch(r"[0-9a-f]{64}", str(expected).lower()):
                raise UpdateScenarioError(f"write change {index} has invalid expectedSha256")
        elif op == "delete":
            expected = str(change.get("expectedSha256") or "").lower()
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise UpdateScenarioError(f"delete change {index} requires expectedSha256")
        elif op == "assert_contains":
            text = change.get("text")
            if not isinstance(text, str) or not text:
                raise UpdateScenarioError(f"assert_contains change {index} requires non-empty text")
        changes.append(change)

    return UpdateScenario(
        path=path,
        digest=_sha256_bytes(raw),
        from_version=from_version,
        to_version=to_version,
        title=title,
        commit_message=commit_message,
        base_blobs=base_blobs,
        changes=tuple(changes),
    )


def scenario_marker(scenario: UpdateScenario) -> str:
    return f"Agent-DevTools-Scenario-SHA256: {scenario.digest}"


def _project_version(root: Path) -> str:
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise UpdateScenarioError(f"cannot read project VERSION: {exc}") from exc


def _assert_base(root: Path, scenario: UpdateScenario) -> None:
    version = _project_version(root)
    if version != scenario.from_version:
        raise UpdateScenarioError(f"scenario expects VERSION {scenario.from_version}, found {version}")
    for rel, expected in scenario.base_blobs.items():
        path = _path(root, rel)
        if not path.is_file():
            raise UpdateScenarioError(f"baseline file missing: {rel}")
        actual = _git_blob_sha1(path.read_bytes())
        if actual != expected:
            raise UpdateScenarioError(f"baseline blob mismatch {rel}: expected {expected}, found {actual}")


def apply_scenario(root: Path, scenario: UpdateScenario) -> dict[str, Any]:
    root = root.resolve()
    _assert_base(root, scenario)
    applied = 0
    unchanged = 0
    rows: list[dict[str, str]] = []

    for change in scenario.changes:
        op = str(change["op"])
        rel = str(change["path"])
        path = _path(root, rel)

        if op == "assert_contains":
            if not path.is_file():
                raise UpdateScenarioError(f"assert_contains file missing: {rel}")
            if str(change["text"]) not in path.read_text(encoding="utf-8"):
                raise UpdateScenarioError(f"assert_contains failed: {rel}")
            unchanged += 1
            rows.append({"op": op, "path": rel, "status": "pass"})
            continue

        if op == "replace":
            if not path.is_file():
                raise UpdateScenarioError(f"replace file missing: {rel}")
            text = path.read_text(encoding="utf-8")
            before = str(change["before"])
            after = str(change["after"])
            before_count = text.count(before)
            after_count = text.count(after)
            if before_count == 1 and after_count == 0:
                path.write_text(text.replace(before, after, 1), encoding="utf-8")
                applied += 1
                rows.append({"op": op, "path": rel, "status": "applied"})
            elif before_count == 0 and after_count == 1:
                unchanged += 1
                rows.append({"op": op, "path": rel, "status": "already-applied"})
            else:
                raise UpdateScenarioError(
                    f"replace state is ambiguous for {rel}: before={before_count}, after={after_count}"
                )
            continue

        if op == "write":
            desired = str(change["content"]).encode("utf-8")
            if path.exists():
                if not path.is_file():
                    raise UpdateScenarioError(f"write target is not a file: {rel}")
                current = path.read_bytes()
                if current == desired:
                    unchanged += 1
                    rows.append({"op": op, "path": rel, "status": "already-applied"})
                    continue
                expected = change.get("expectedSha256")
                if expected is None:
                    raise UpdateScenarioError(
                        f"write refuses to overwrite differing file without expectedSha256: {rel}"
                    )
                actual = _sha256_bytes(current)
                if actual != str(expected).lower():
                    raise UpdateScenarioError(
                        f"write preimage mismatch {rel}: expected {expected}, found {actual}"
                    )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(desired)
            applied += 1
            rows.append({"op": op, "path": rel, "status": "applied"})
            continue

        if op == "delete":
            if not path.exists():
                unchanged += 1
                rows.append({"op": op, "path": rel, "status": "already-applied"})
                continue
            if not path.is_file():
                raise UpdateScenarioError(f"delete target is not a file: {rel}")
            actual = _sha256_bytes(path.read_bytes())
            expected = str(change["expectedSha256"]).lower()
            if actual != expected:
                raise UpdateScenarioError(
                    f"delete preimage mismatch {rel}: expected {expected}, found {actual}"
                )
            path.unlink()
            applied += 1
            rows.append({"op": op, "path": rel, "status": "applied"})

    return {
        "format": "agent-devtools-update-scenario-result",
        "formatVersion": 1,
        "scenarioSha256": scenario.digest,
        "fromVersion": scenario.from_version,
        "toVersion": scenario.to_version,
        "applied": applied,
        "unchanged": unchanged,
        "changes": rows,
    }
