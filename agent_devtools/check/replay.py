from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from agent_devtools.core.archive import ArchiveSafetyError, extract_zip_bounded, read_zip_bounded
from agent_devtools.core.hashing import sha256_file, stable_fingerprint
from agent_devtools.core.pathmatch import matches_any

from .certification import certify_plan
from .config import CheckConfig, CheckConfigError, load_check_config
from .contracts import capture_hashes
from .policy import PolicyError, load_policy
from .selection import apply_selection_safety_guards

BUNDLE_FORMAT = "agent-devtools-exact-replay-bundle"
BUNDLE_VERSION = 1
SOURCE_SCHEMA = "agent-devtools-canonical-source-v1"
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_SOURCE_MANIFEST_HEADER = "# agent-devtools-source-package-manifest-v1"


class ReplayError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceInventory:
    hashes: dict[str, str]
    fingerprint: str

    @property
    def files(self) -> int:
        return len(self.hashes)


def canonical_source_hashes(root: Path, config: CheckConfig) -> SourceInventory:
    root = root.resolve()
    include = config.replay.source_include
    exclude = (*config.ignore, *config.replay.source_exclude)
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if exclude and matches_any(rel, exclude):
            continue
        if not matches_any(rel, include):
            continue
        if path.is_symlink():
            raise ReplayError(
                f"canonical source contains symlink {rel!r}; ignore/exclude it or replace it with a regular file"
            )
        if path.is_file():
            hashes[rel] = sha256_file(path)
    fingerprint = stable_fingerprint({"schema": SOURCE_SCHEMA, "files": hashes})
    return SourceInventory(hashes=hashes, fingerprint=fingerprint)


def _copy_inventory(source_root: Path, destination: Path, inventory: SourceInventory) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for rel in inventory.hashes:
        src = source_root / rel
        dst = destination / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _safe_zip_name(name: str) -> PurePosixPath:
    item = PurePosixPath(name)
    if item.is_absolute() or ".." in item.parts:
        raise ReplayError(f"unsafe archive path: {name!r}")
    return item


def _extract_zip_safely(archive_path: Path, destination: Path) -> None:
    try:
        extract_zip_bounded(archive_path, destination)
    except ArchiveSafetyError as exc:
        raise ReplayError(f"cannot extract base snapshot {archive_path}: {exc}") from exc


def _locate_project_root(extracted: Path) -> Path:
    """Locate the consumer project root without mistaking vendored tools for projects.

    A source snapshot may legitimately contain nested Agent DevTools copies with their
    own agent-tools.json. The consumer project is the unique shallowest config root;
    deeper configs belong to vendored/internal tooling. Ambiguity is retained only
    when multiple configs exist at the same shallowest depth.
    """
    direct = extracted / "agent-tools.json"
    if direct.is_file():
        return extracted

    configs = sorted(extracted.rglob("agent-tools.json"))
    if configs:
        ranked = sorted((len(path.relative_to(extracted).parts), path) for path in configs)
        shallowest_depth = ranked[0][0]
        shallowest = [path for depth, path in ranked if depth == shallowest_depth]
        if len(shallowest) == 1:
            return shallowest[0].parent
        roots = ", ".join(path.parent.relative_to(extracted).as_posix() or "." for path in shallowest)
        raise ReplayError(
            "base snapshot contains multiple equally shallow agent-tools.json files; "
            f"cannot determine the consumer project root: {roots}"
        )

    children = [item for item in extracted.iterdir() if item.is_dir()]
    if len(children) == 1:
        return children[0]
    return extracted


def _strip_embedded_source_manifest(project_root: Path) -> None:
    manifest = project_root / "MANIFEST.sha256"
    if not manifest.is_file():
        return
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return
    if not lines or lines[0].strip() != _SOURCE_MANIFEST_HEADER:
        return
    expected: dict[str, str] = {}
    for raw in lines[1:]:
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            digest, rel = raw.split("  ", 1)
        except ValueError as exc:
            raise ReplayError("embedded source package MANIFEST.sha256 is malformed") from exc
        _safe_zip_name(rel)
        if rel in expected:
            raise ReplayError(f"embedded source package manifest contains duplicate path: {rel}")
        expected[rel] = digest
    actual = {
        path.relative_to(project_root).as_posix(): sha256_file(path)
        for path in sorted(project_root.rglob("*"))
        if path.is_file() and path != manifest
    }
    if actual != expected:
        raise ReplayError("embedded source package MANIFEST.sha256 does not match package payload")
    manifest.unlink()


def materialize_base(base: Path, destination: Path) -> Path:
    base = base.resolve()
    if base.is_dir():
        shutil.copytree(base, destination, dirs_exist_ok=True)
        return destination
    if not base.is_file():
        raise ReplayError(f"base snapshot not found: {base}")
    if not zipfile.is_zipfile(base):
        raise ReplayError(f"base snapshot must be a directory or ZIP archive: {base}")
    _extract_zip_safely(base, destination)
    project_root = _locate_project_root(destination)
    _strip_embedded_source_manifest(project_root)
    return project_root


def _zip_write_bytes(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, _FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


def create_exact_bundle(
    *,
    base_root: Path,
    target_root: Path,
    config: CheckConfig,
    bundle_path: Path,
) -> dict[str, Any]:
    base_inventory = canonical_source_hashes(base_root, config)
    target_inventory = canonical_source_hashes(target_root, config)
    base_paths = set(base_inventory.hashes)
    target_paths = set(target_inventory.hashes)
    added = sorted(target_paths - base_paths)
    deleted = sorted(base_paths - target_paths)
    changed = sorted(
        path
        for path in base_paths & target_paths
        if base_inventory.hashes[path] != target_inventory.hashes[path]
    )
    payload_paths = added + changed
    manifest = {
        "format": BUNDLE_FORMAT,
        "formatVersion": BUNDLE_VERSION,
        "base": {
            "fingerprint": base_inventory.fingerprint,
            "files": base_inventory.files,
        },
        "target": {
            "fingerprint": target_inventory.fingerprint,
            "files": target_inventory.files,
        },
        "delta": {
            "added": added,
            "changed": changed,
            "deleted": deleted,
            "payload": {path: target_inventory.hashes[path] for path in payload_paths},
        },
        "sourceRules": {
            "include": list(config.replay.source_include),
            "exclude": list(config.replay.source_exclude),
            "projectIgnore": list(config.ignore),
        },
    }
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = bundle_path.with_name(bundle_path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    with zipfile.ZipFile(tmp, "w") as archive:
        manifest_bytes = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        _zip_write_bytes(archive, "replay-manifest.json", manifest_bytes)
        for rel in payload_paths:
            _zip_write_bytes(archive, f"files/{rel}", (target_root / rel).read_bytes())
    os.replace(tmp, bundle_path)
    return manifest


def read_bundle_manifest(bundle_path: Path) -> dict[str, Any]:
    try:
        payloads = read_zip_bounded(bundle_path)
        raw = payloads["replay-manifest.json"]
    except (ArchiveSafetyError, KeyError) as exc:
        raise ReplayError(f"invalid replay bundle {bundle_path}: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplayError(f"replay bundle manifest is invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReplayError("replay bundle manifest root must be an object")
    if payload.get("format") != BUNDLE_FORMAT or int(payload.get("formatVersion") or 0) != BUNDLE_VERSION:
        raise ReplayError("unsupported replay bundle format")
    return payload


def apply_exact_bundle(
    *,
    base_root: Path,
    replay_root: Path,
    config: CheckConfig,
    bundle_path: Path,
) -> dict[str, Any]:
    manifest = read_bundle_manifest(bundle_path)
    base_inventory = canonical_source_hashes(base_root, config)
    expected_base = str(((manifest.get("base") or {}).get("fingerprint") or ""))
    if base_inventory.fingerprint != expected_base:
        raise ReplayError(
            "base source fingerprint does not match the replay bundle: "
            f"expected {expected_base}, got {base_inventory.fingerprint}"
        )

    if replay_root.exists():
        shutil.rmtree(replay_root)
    _copy_inventory(base_root, replay_root, base_inventory)
    delta = manifest.get("delta") if isinstance(manifest.get("delta"), dict) else {}
    deleted = delta.get("deleted") if isinstance(delta.get("deleted"), list) else []
    payload = delta.get("payload") if isinstance(delta.get("payload"), dict) else {}
    for rel in deleted:
        if not isinstance(rel, str):
            raise ReplayError("replay bundle deleted paths must be strings")
        _safe_zip_name(rel)
        path = replay_root / rel
        if path.is_file() or path.is_symlink():
            path.unlink()
        parent = path.parent
        while parent != replay_root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    try:
        bundle_payloads = read_zip_bounded(bundle_path)
    except ArchiveSafetyError as exc:
        raise ReplayError(f"invalid replay bundle: {exc}") from exc
    for rel, expected_hash in sorted(payload.items()):
        if not isinstance(rel, str) or not isinstance(expected_hash, str):
            raise ReplayError("replay bundle payload entries must be path/hash strings")
        _safe_zip_name(rel)
        archive_name = f"files/{rel}"
        _safe_zip_name(archive_name)
        data = bundle_payloads.get(archive_name)
        if data is None:
            raise ReplayError(f"replay bundle is missing payload bytes for {rel}")
        target = replay_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if sha256_file(target) != expected_hash:
            raise ReplayError(f"replay bundle payload hash mismatch for {rel}")

    replay_inventory = canonical_source_hashes(replay_root, config)
    expected_target = str(((manifest.get("target") or {}).get("fingerprint") or ""))
    if replay_inventory.fingerprint != expected_target:
        raise ReplayError(
            "replayed source fingerprint does not match target bundle fingerprint: "
            f"expected {expected_target}, got {replay_inventory.fingerprint}"
        )
    return manifest


def compare_inventories(target: SourceInventory, replay: SourceInventory) -> dict[str, Any]:
    target_paths = set(target.hashes)
    replay_paths = set(replay.hashes)
    missing = sorted(target_paths - replay_paths)
    extra = sorted(replay_paths - target_paths)
    mismatch = sorted(
        path for path in target_paths & replay_paths if target.hashes[path] != replay.hashes[path]
    )
    return {
        "targetFiles": target.files,
        "replayFiles": replay.files,
        "identical": target.files - len(missing) - len(mismatch),
        "missing": missing,
        "extra": extra,
        "mismatch": mismatch,
        "targetFingerprint": target.fingerprint,
        "replayFingerprint": replay.fingerprint,
    }


def _attach_shared_paths(target_root: Path, replay_root: Path, paths: Iterable[str]) -> dict[str, str]:
    modes: dict[str, str] = {}
    for rel in paths:
        source = target_root / rel
        if not source.exists():
            raise ReplayError(f"replay shared path does not exist in target project: {rel}")
        destination = replay_root / rel
        if destination.exists() or destination.is_symlink():
            raise ReplayError(f"replay shared path collides with canonical source: {rel}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.symlink_to(source, target_is_directory=source.is_dir())
            modes[rel] = "symlink"
        except OSError:
            if source.is_dir():
                shutil.copytree(source, destination, symlinks=True)
            else:
                shutil.copy2(source, destination)
            modes[rel] = "copy"
    return modes


def _certification_plan(config: CheckConfig):
    policy = load_policy(config.policy_path)
    profile_id = config.certification.profile
    profile = policy.profiles.get(profile_id)
    if profile is None:
        raise ReplayError(f"unknown certification profile: {profile_id}")
    if profile.selection != "all":
        raise ReplayError(f"certification profile {profile_id!r} must use selection='all'")
    plan = apply_selection_safety_guards(policy.plan(profile_id, ()), config)
    selected = [str(item.get("id")) for item in plan.selected]
    if selected != list(config.suite_order):
        raise ReplayError(
            "certification profile must select the complete configured suiteOrder; "
            f"selected={selected}, suiteOrder={list(config.suite_order)}"
        )
    return plan


def _captured_output_hashes(root: Path, config: CheckConfig) -> dict[str, str]:
    patterns: list[str] = []
    for suite in config.suite_order:
        spec = config.commands.get(suite)
        if spec is None:
            continue
        for pattern in spec.outputs.capture:
            if pattern not in patterns:
                patterns.append(pattern)
    return capture_hashes(root, patterns)


def run_exact_replay(
    *,
    target_root: Path,
    base: Path,
    config_path: Path | None = None,
    bundle_out: Path | None = None,
    source_only: bool = False,
) -> tuple[int, dict[str, Any]]:
    target_root = target_root.resolve()
    base = base.resolve()
    try:
        target_config = load_check_config(target_root, config_path)
    except CheckConfigError as exc:
        raise ReplayError(str(exc)) from exc
    config_rel: Path | None = None
    if config_path is not None:
        resolved_config = config_path if config_path.is_absolute() else (target_root / config_path)
        try:
            config_rel = resolved_config.resolve().relative_to(target_root)
        except ValueError as exc:
            raise ReplayError("replay config must be inside the target project root") from exc

    target_certification: dict[str, Any] | None = None
    if not source_only:
        try:
            plan = _certification_plan(target_config)
            code, cert_report = certify_plan(target_root, target_config, plan, cold=False)
        except (PolicyError, CheckConfigError) as exc:
            raise ReplayError(str(exc)) from exc
        target_certification = {
            "status": cert_report.get("status"),
            "runDirectory": cert_report.get("runDirectory"),
            "certification": cert_report.get("certification"),
        }
        if code != 0:
            return 1, {
                "format": "agent-devtools-exact-replay-report",
                "formatVersion": 1,
                "status": "fail",
                "stage": "target-certification",
                "targetCertification": target_certification,
            }

    with tempfile.TemporaryDirectory(prefix="agent-devtools-replay-") as tmp:
        temp_root = Path(tmp)
        extracted = temp_root / "base-extracted"
        extracted.mkdir()
        base_root = materialize_base(base, extracted)
        target_inventory = canonical_source_hashes(target_root, target_config)
        base_inventory = canonical_source_hashes(base_root, target_config)

        ephemeral_bundle = temp_root / "exact-replay.agent-replay.zip"
        selected_bundle = bundle_out.resolve() if bundle_out is not None else ephemeral_bundle
        manifest = create_exact_bundle(
            base_root=base_root,
            target_root=target_root,
            config=target_config,
            bundle_path=selected_bundle,
        )
        bundle_sha256 = sha256_file(selected_bundle)

        replay_root = temp_root / "replay"
        apply_exact_bundle(
            base_root=base_root,
            replay_root=replay_root,
            config=target_config,
            bundle_path=selected_bundle,
        )
        replay_inventory = canonical_source_hashes(replay_root, target_config)
        source_compare = compare_inventories(target_inventory, replay_inventory)
        report: dict[str, Any] = {
            "format": "agent-devtools-exact-replay-report",
            "formatVersion": 1,
            "status": "pass",
            "sourceOnly": source_only,
            "base": {
                "path": str(base),
                "files": base_inventory.files,
                "fingerprint": base_inventory.fingerprint,
            },
            "target": {
                "files": target_inventory.files,
                "fingerprint": target_inventory.fingerprint,
            },
            "bundle": {
                "path": str(bundle_out.resolve()) if bundle_out is not None else None,
                "sha256": bundle_sha256,
                "added": len((manifest.get("delta") or {}).get("added") or []),
                "changed": len((manifest.get("delta") or {}).get("changed") or []),
                "deleted": len((manifest.get("delta") or {}).get("deleted") or []),
            },
            "source": source_compare,
            "targetCertification": target_certification,
        }
        if source_compare["missing"] or source_compare["extra"] or source_compare["mismatch"]:
            report["status"] = "fail"
            report["stage"] = "source-compare"
            return 1, report

        if source_only:
            return 0, report

        shared = _attach_shared_paths(target_root, replay_root, target_config.replay.shared_paths)
        replay_config = load_check_config(replay_root, config_rel)
        replay_plan = _certification_plan(replay_config)
        code, replay_cert_report = certify_plan(replay_root, replay_config, replay_plan, cold=True)
        report["sharedPaths"] = shared
        report["replayCertification"] = {
            "status": replay_cert_report.get("status"),
            "certification": replay_cert_report.get("certification"),
        }
        if code != 0:
            report["status"] = "fail"
            report["stage"] = "replay-certification"
            report["replayCertification"]["message"] = replay_cert_report.get("message")
            return 1, report

        target_generated = _captured_output_hashes(target_root, target_config)
        replay_generated = _captured_output_hashes(replay_root, replay_config)
        keys = set(target_generated) | set(replay_generated)
        generated_mismatch = sorted(
            path for path in keys if target_generated.get(path) != replay_generated.get(path)
        )
        report["generated"] = {
            "targetFiles": len(target_generated),
            "replayFiles": len(replay_generated),
            "matching": len(keys) - len(generated_mismatch),
            "mismatch": generated_mismatch,
            "targetFingerprint": stable_fingerprint(target_generated),
            "replayFingerprint": stable_fingerprint(replay_generated),
        }
        if generated_mismatch:
            report["status"] = "fail"
            report["stage"] = "generated-compare"
            return 1, report
        return 0, report
