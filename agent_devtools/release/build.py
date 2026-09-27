from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from agent_devtools import __version__ as engine_version
from agent_devtools.check.config import CheckConfigError, load_check_config
from agent_devtools.check.replay import ReplayError, run_exact_replay
from agent_devtools.core.hashing import sha256_file
from agent_devtools.work.knowledge import knowledge_snapshot, verify_knowledge_snapshot
from agent_devtools.changes import ChangeSetError, discover_changes

from .config import ReleaseConfigError, load_release_config
from .package import PackageError, package_inventory, verify_package_manifest, write_package_manifest, write_package_zip

_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


class ReleaseBuildError(RuntimeError):
    pass


def _validate_version(version: str) -> str:
    version = version.strip()
    if not _SAFE_VERSION.fullmatch(version):
        raise ReleaseBuildError("release version must use only letters, digits, dot, underscore, plus and dash")
    return version


def build_release(
    *,
    root: Path,
    version: str,
    base: Path,
    out_dir: Path,
    config_path: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    root = root.resolve()
    version = _validate_version(version)
    try:
        check_config = load_check_config(root, config_path)
        release_config = load_release_config(root, config_path)
    except (CheckConfigError, ReleaseConfigError) as exc:
        raise ReleaseBuildError(str(exc)) from exc

    out_dir = out_dir.resolve()
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ReleaseBuildError(f"release output directory must be absent or empty: {out_dir}")
    prefix = release_config.artifact_prefix

    with tempfile.TemporaryDirectory(prefix="agent-devtools-release-replay-") as tmp:
        temp_bundle = Path(tmp) / "exact.agent-replay.zip"
        try:
            replay_code, replay_report = run_exact_replay(
                target_root=root,
                base=base,
                config_path=config_path,
                bundle_out=temp_bundle,
                source_only=False,
            )
        except ReplayError as exc:
            raise ReleaseBuildError(str(exc)) from exc
        if replay_code != 0:
            return 1, {
                "format": "agent-devtools-release-report",
                "formatVersion": 1,
                "status": "fail",
                "stage": "exact-replay",
                "replay": replay_report,
            }

        # Certification/replay may have produced generated runtime files. Freeze every
        # package inventory after that build proof but before writing any release output.
        try:
            inventories = {
                spec.package_id: package_inventory(root, spec, check_config)
                for spec in release_config.packages
            }
        except PackageError as exc:
            raise ReleaseBuildError(str(exc)) from exc

        replay_name = f"{prefix}-{version}.agent-replay.zip"
        frozen_bundle = Path(tmp) / replay_name
        shutil.copy2(temp_bundle, frozen_bundle)

        stage_dir = Path(tempfile.mkdtemp(prefix=".agent-release-stage-", dir=str(out_dir.parent)))
        try:
            replay_out = stage_dir / replay_name
            shutil.copy2(frozen_bundle, replay_out)
            package_rows: list[dict[str, Any]] = []
            for spec in release_config.packages:
                inventory = inventories[spec.package_id]
                archive_name = f"{prefix}-{version}-{spec.package_id}.zip"
                archive = stage_dir / archive_name
                archive_root = f"{prefix}-{version}"
                write_package_zip(
                    root=root,
                    inventory=inventory,
                    archive_path=archive,
                    archive_root=archive_root,
                    embed_source_manifest=(spec.kind == "source"),
                )
                manifest_path = stage_dir / f"{archive_name}.manifest.json"
                manifest = write_package_manifest(
                    manifest_path=manifest_path,
                    project=prefix,
                    version=version,
                    spec=spec,
                    inventory=inventory,
                    archive_path=archive,
                )
                package_rows.append({
                    "id": spec.package_id,
                    "kind": spec.kind,
                    "archive": archive_name,
                    "archiveSha256": manifest["archiveSha256"],
                    "archiveBytes": manifest["archiveBytes"],
                    "manifest": manifest_path.name,
                    "manifestSha256": sha256_file(manifest_path),
                    "files": inventory.files,
                    "fingerprint": inventory.fingerprint,
                })

            knowledge_payload = knowledge_snapshot(
                root,
                release_version=version,
                source_fingerprint=((replay_report.get("target") or {}).get("fingerprint")),
            )
            knowledge_name = "AGENT-KNOWLEDGE-SNAPSHOT.json"
            knowledge_path = stage_dir / knowledge_name
            knowledge_path.write_text(json.dumps(knowledge_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

            try:
                change_set = discover_changes(root, config_path)
            except ChangeSetError as exc:
                change_set = {"format": "agent-devtools-change-set", "formatVersion": 1, "available": False, "reason": str(exc)}

            evidence = {
                "format": "agent-devtools-release-evidence",
                "formatVersion": 1,
                "project": prefix,
                "version": version,
                "engineVersion": engine_version,
                "status": "pass",
                "base": {
                    "file": base.name,
                    "fingerprint": ((replay_report.get("base") or {}).get("fingerprint")),
                    "files": ((replay_report.get("base") or {}).get("files")),
                },
                "target": {
                    "fingerprint": ((replay_report.get("target") or {}).get("fingerprint")),
                    "files": ((replay_report.get("target") or {}).get("files")),
                },
                "replay": {
                    "artifact": replay_name,
                    "sha256": sha256_file(replay_out),
                    "source": replay_report.get("source"),
                    "generated": replay_report.get("generated"),
                },
                "changeSet": {
                    "available": bool(change_set.get("available")),
                    "canonicalChangedFiles": list(change_set.get("canonicalChangedFiles", [])),
                    "canonicalUntrackedFiles": list(change_set.get("canonicalUntrackedFiles", [])),
                    "knowledgeChangedFiles": list(change_set.get("knowledgeChangedFiles", [])),
                },
                "knowledge": {
                    "artifact": knowledge_name,
                    "sha256": sha256_file(knowledge_path),
                    "fingerprint": knowledge_payload["knowledgeFingerprint"],
                    "records": knowledge_payload["recordCount"],
                },
                "packages": package_rows,
            }
            evidence_path = stage_dir / "RELEASE-EVIDENCE.json"
            evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

            checksum_entries: list[tuple[str, str]] = [(replay_name, sha256_file(replay_out)), (knowledge_name, sha256_file(knowledge_path))]
            for row in package_rows:
                checksum_entries.append((row["archive"], row["archiveSha256"]))
                checksum_entries.append((row["manifest"], row["manifestSha256"]))
            checksum_entries.append((evidence_path.name, sha256_file(evidence_path)))
            (stage_dir / "SHA256SUMS.txt").write_text(
                "".join(f"{digest}  {name}\n" for name, digest in checksum_entries),
                encoding="utf-8",
            )
            verify_code, verify_report = verify_release(stage_dir)
            if verify_code != 0:
                raise ReleaseBuildError("staged release verification failed: " + "; ".join(verify_report["failures"]))
            if out_dir.exists():
                out_dir.rmdir()
            os.replace(stage_dir, out_dir)
            return 0, evidence
        except Exception:
            shutil.rmtree(stage_dir, ignore_errors=True)
            raise


def verify_release(out_dir: Path) -> tuple[int, dict[str, Any]]:
    out_dir = out_dir.resolve()
    evidence_path = out_dir / "RELEASE-EVIDENCE.json"
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseBuildError(f"cannot read release evidence: {exc}") from exc
    if not isinstance(evidence, dict) or evidence.get("format") != "agent-devtools-release-evidence":
        raise ReleaseBuildError("unsupported release evidence format")
    failures: list[str] = []
    replay = evidence.get("replay") if isinstance(evidence.get("replay"), dict) else {}
    replay_name = str(replay.get("artifact") or "")
    replay_path = out_dir / replay_name
    if not replay_path.is_file():
        failures.append(f"missing replay artifact: {replay_name}")
    elif sha256_file(replay_path) != str(replay.get("sha256") or ""):
        failures.append(f"replay hash mismatch: {replay_name}")
    knowledge = evidence.get("knowledge") if isinstance(evidence.get("knowledge"), dict) else {}
    knowledge_name = str(knowledge.get("artifact") or "")
    knowledge_path = out_dir / knowledge_name
    if not knowledge_path.is_file():
        failures.append(f"missing knowledge snapshot: {knowledge_name}")
    else:
        if sha256_file(knowledge_path) != str(knowledge.get("sha256") or ""):
            failures.append(f"knowledge snapshot hash mismatch: {knowledge_name}")
        try:
            knowledge_payload = json.loads(knowledge_path.read_text(encoding="utf-8"))
            failures.extend(verify_knowledge_snapshot(knowledge_payload))
            if str(knowledge_payload.get("knowledgeFingerprint") or "") != str(knowledge.get("fingerprint") or ""):
                failures.append("knowledge evidence fingerprint mismatch")
        except Exception as exc:
            failures.append(f"invalid knowledge snapshot {knowledge_name}: {exc}")
    packages = evidence.get("packages") if isinstance(evidence.get("packages"), list) else []
    for row in packages:
        if not isinstance(row, dict):
            failures.append("invalid package evidence row")
            continue
        manifest_name = str(row.get("manifest") or "")
        manifest_path = out_dir / manifest_name
        if not manifest_path.is_file():
            failures.append(f"missing package manifest: {manifest_name}")
            continue
        if sha256_file(manifest_path) != str(row.get("manifestSha256") or ""):
            failures.append(f"manifest hash mismatch: {manifest_name}")
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append(f"invalid package manifest {manifest_name}: {exc}")
            continue
        failures.extend(verify_package_manifest(out_dir, manifest))
    sums = out_dir / "SHA256SUMS.txt"
    if not sums.is_file():
        failures.append("missing SHA256SUMS.txt")
    else:
        for raw in sums.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                expected, name = raw.split("  ", 1)
            except ValueError:
                failures.append("malformed SHA256SUMS.txt")
                break
            path = out_dir / name
            if not path.is_file():
                failures.append(f"SHA256SUMS missing file: {name}")
            elif sha256_file(path) != expected:
                failures.append(f"SHA256SUMS mismatch: {name}")
    return (1 if failures else 0), {
        "format": "agent-devtools-release-verification",
        "formatVersion": 1,
        "status": "fail" if failures else "pass",
        "project": evidence.get("project"),
        "version": evidence.get("version"),
        "packages": len(packages),
        "failures": failures,
    }
