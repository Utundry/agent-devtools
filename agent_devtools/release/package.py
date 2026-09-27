from __future__ import annotations

import json
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from agent_devtools.check.config import CheckConfig
from agent_devtools.check.replay import canonical_source_hashes
from agent_devtools.core.hashing import sha256_file, stable_fingerprint
from agent_devtools.core.pathmatch import matches_any

from .config import PackageSpec

_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_SOURCE_MANIFEST_NAME = "MANIFEST.sha256"
_SOURCE_MANIFEST_HEADER = "# agent-devtools-source-package-manifest-v1"


class PackageError(RuntimeError):
    pass


@dataclass(frozen=True)
class PackageInventory:
    hashes: dict[str, str]
    fingerprint: str

    @property
    def files(self) -> int:
        return len(self.hashes)


def _files_inventory(root: Path, spec: PackageSpec) -> PackageInventory:
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            if matches_any(rel, spec.include) and not matches_any(rel, spec.exclude):
                raise PackageError(f"release package {spec.package_id!r} contains symlink {rel!r}; exclude it explicitly")
            continue
        if not path.is_file():
            continue
        if not matches_any(rel, spec.include) or matches_any(rel, spec.exclude):
            continue
        hashes[rel] = sha256_file(path)
    for pattern in spec.required:
        if not any(matches_any(rel, (pattern,)) for rel in hashes):
            raise PackageError(f"release package {spec.package_id!r} missing required file/glob: {pattern}")
    return PackageInventory(hashes, stable_fingerprint({"schema": "agent-devtools-release-package-v1", "files": hashes}))


def package_inventory(root: Path, spec: PackageSpec, check_config: CheckConfig) -> PackageInventory:
    if spec.kind == "source":
        source = canonical_source_hashes(root, check_config)
        return PackageInventory(source.hashes, source.fingerprint)
    return _files_inventory(root, spec)


def source_manifest_bytes(inventory: PackageInventory) -> bytes:
    lines = [_SOURCE_MANIFEST_HEADER]
    lines.extend(f"{digest}  {rel}" for rel, digest in sorted(inventory.hashes.items()))
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_package_zip(
    *,
    root: Path,
    inventory: PackageInventory,
    archive_path: Path,
    archive_root: str,
    embed_source_manifest: bool = False,
) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = archive_path.with_name(archive_path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    if embed_source_manifest and _SOURCE_MANIFEST_NAME in inventory.hashes:
        raise PackageError(
            f"source package reserves {_SOURCE_MANIFEST_NAME!r} for package integrity metadata; "
            "exclude or rename the project file"
        )
    with zipfile.ZipFile(tmp, "w") as archive:
        for rel in inventory.hashes:
            path = root / rel
            info = zipfile.ZipInfo(f"{archive_root}/{rel}", _FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
        if embed_source_manifest:
            info = zipfile.ZipInfo(f"{archive_root}/{_SOURCE_MANIFEST_NAME}", _FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source_manifest_bytes(inventory))
    os.replace(tmp, archive_path)


def write_package_manifest(
    *,
    manifest_path: Path,
    project: str,
    version: str,
    spec: PackageSpec,
    inventory: PackageInventory,
    archive_path: Path,
) -> dict:
    payload = {
        "format": "agent-devtools-package-manifest",
        "formatVersion": 1,
        "project": project,
        "version": version,
        "package": spec.package_id,
        "kind": spec.kind,
        "files": inventory.files,
        "fingerprint": inventory.fingerprint,
        "archive": archive_path.name,
        "archiveSha256": sha256_file(archive_path),
        "archiveBytes": archive_path.stat().st_size,
        "content": inventory.hashes,
    }
    if spec.kind == "source":
        import hashlib
        manifest_bytes = source_manifest_bytes(inventory)
        payload["embeddedManifest"] = {
            "path": _SOURCE_MANIFEST_NAME,
            "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "files": inventory.files,
        }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return payload


def verify_package_manifest(base_dir: Path, payload: dict) -> list[str]:
    failures: list[str] = []
    archive_name = str(payload.get("archive") or "")
    archive = base_dir / archive_name
    if not archive.is_file():
        return [f"missing package archive: {archive_name}"]
    expected_archive = str(payload.get("archiveSha256") or "")
    actual_archive = sha256_file(archive)
    if actual_archive != expected_archive:
        failures.append(f"archive hash mismatch: {archive_name}")
    expected_content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
    prefix = None
    try:
        with zipfile.ZipFile(archive) as z:
            files = [name for name in z.namelist() if not name.endswith("/")]
            if files:
                prefix = files[0].split("/", 1)[0]
            actual: dict[str, str] = {}
            embedded_manifest_bytes: bytes | None = None
            for name in files:
                if prefix is None or not name.startswith(prefix + "/"):
                    failures.append(f"unexpected archive entry: {name}")
                    continue
                rel = name[len(prefix) + 1:]
                if payload.get("kind") == "source" and rel == _SOURCE_MANIFEST_NAME:
                    embedded_manifest_bytes = z.read(name)
                    continue
                import hashlib
                actual[rel] = hashlib.sha256(z.read(name)).hexdigest()
    except (OSError, zipfile.BadZipFile) as exc:
        return failures + [f"cannot read package archive {archive_name}: {exc}"]
    normalized_expected = {str(k): str(v) for k, v in expected_content.items()}
    if payload.get("kind") == "source":
        import hashlib
        expected_manifest = source_manifest_bytes(PackageInventory(normalized_expected, str(payload.get("fingerprint") or "")))
        manifest_meta = payload.get("embeddedManifest") if isinstance(payload.get("embeddedManifest"), dict) else {}
        if embedded_manifest_bytes is None:
            failures.append(f"source package is missing embedded {_SOURCE_MANIFEST_NAME}")
        else:
            if embedded_manifest_bytes != expected_manifest:
                failures.append(f"embedded {_SOURCE_MANIFEST_NAME} content mismatch: {archive_name}")
            expected_manifest_hash = str(manifest_meta.get("sha256") or "")
            if expected_manifest_hash and hashlib.sha256(embedded_manifest_bytes).hexdigest() != expected_manifest_hash:
                failures.append(f"embedded {_SOURCE_MANIFEST_NAME} hash mismatch: {archive_name}")
    if actual != normalized_expected:
        failures.append(f"package content mismatch: {archive_name}")
    expected_fp = str(payload.get("fingerprint") or "")
    schema = "agent-devtools-canonical-source-v1" if payload.get("kind") == "source" else "agent-devtools-release-package-v1"
    actual_fp = stable_fingerprint({"schema": schema, "files": actual})
    if actual_fp != expected_fp:
        failures.append(f"package fingerprint mismatch: {archive_name}")
    return failures
