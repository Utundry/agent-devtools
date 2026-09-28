#!/usr/bin/env python3
"""Build and verify Agent DevTools bootstrap artifacts from the current source tree.

The current source tree is authoritative for the runtime payload.  The previously
committed bootstrap kit is used only as a template for the small bootstrap wrapper
files; its payload is discarded and rebuilt from agent_devtools.bootstrap's own
canonical vendor-file iterator.

Typical release flow::

    python scripts/build_bootstrap.py --version 0.8.0
    python scripts/build_bootstrap.py --version 0.8.0 --check

Both commands fail when agent_devtools.__version__ does not equal --version.  This
keeps source version, kit metadata and the single-file installer in lock-step.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_DIR = ROOT / "bootstrap"
DEFAULT_KIT = BOOTSTRAP_DIR / "agent-devtools-bootstrap-kit.zip"
DEFAULT_INSTALLER = BOOTSTRAP_DIR / "AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py"
KIT_PREFIX = ".agent-devtools-bootstrap-kit/"
MANIFEST_REL = "MANIFEST.sha256"
WRAPPER_REQUIRED = (
    "AGENT-READ-ME-FIRST.md",
    "BOOTSTRAP_AGENT_DEVTOOLS.py",
    "bootstrap-kit.json",
)
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
SEMVERISH_RE = re.compile(r"(?<![0-9])0\.[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[-+][A-Za-z0-9._-]+)?")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class BootstrapBuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuildResult:
    version: str
    source_version: str
    source_head: str | None
    payload_files: int
    payload_fingerprint: str
    kit_bytes: bytes
    kit_sha256: str
    installer_bytes: bytes
    installer_sha256: str
    old_version: str
    added_payload: tuple[str, ...]
    removed_payload: tuple[str, ...]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_source_version(root: Path) -> str:
    path = root / "agent_devtools" / "__init__.py"
    text = path.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', text, re.MULTILINE)
    if not match:
        raise BootstrapBuildError(f"cannot read __version__ from {path}")
    return match.group(1)


def _git_head(root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and value else None


def _dirty_paths_from_porcelain(text: str) -> tuple[str, ...]:
    paths: list[str] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        if len(raw) < 4:
            paths.append(raw.strip())
            continue
        value = raw[3:]
        if " -> " in value:
            before, after = value.split(" -> ", 1)
            for item in (before, after):
                item = item.strip()
                if item and item not in paths:
                    paths.append(item)
        else:
            value = value.strip()
            if value and value not in paths:
                paths.append(value)
    return tuple(paths)


def _assert_clean_git(root: Path, *, allowed_paths: Iterable[str] = ()) -> None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapBuildError(f"cannot inspect Git working tree: {exc}") from exc
    if proc.returncode != 0:
        raise BootstrapBuildError("cannot inspect Git working tree")
    allowed = {str(item).replace("\\", "/").lstrip("./") for item in allowed_paths}
    dirty = _dirty_paths_from_porcelain(proc.stdout)
    unexpected = [
        path for path in dirty
        if path.replace("\\", "/").lstrip("./") not in allowed
    ]
    if unexpected:
        preview = ", ".join(unexpected[:8])
        suffix = f" (+{len(unexpected) - 8} more)" if len(unexpected) > 8 else ""
        raise BootstrapBuildError(
            "working tree is not clean; commit the exact source candidate before rebuilding bootstrap "
            "or pass --allow-dirty for deliberate local development"
            f"; unexpected dirty path(s): {preview}{suffix}"
        )


def _payload_inventory(root: Path) -> dict[str, bytes]:
    # Keep one canonical definition of the vendored runtime.  Bootstrap itself uses
    # this iterator when installing devtools/agent, so the builder must use it too.
    sys.path.insert(0, str(root))
    try:
        from agent_devtools.bootstrap import _iter_vendor_files  # type: ignore
    finally:
        try:
            sys.path.remove(str(root))
        except ValueError:
            pass
    payload: dict[str, bytes] = {}
    for rel, path in _iter_vendor_files(root):
        key = rel.as_posix()
        payload[key] = path.read_bytes()
    if not payload or "agent.py" not in payload or "agent_devtools/__init__.py" not in payload:
        raise BootstrapBuildError("canonical runtime payload is unexpectedly incomplete")
    return dict(sorted(payload.items()))


def _tree_fingerprint(files: Mapping[str, bytes]) -> str:
    h = hashlib.sha256()
    for rel in sorted(files):
        digest = sha256_bytes(files[rel])
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(digest.encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def _read_kit_entries(data: bytes) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(_BytesReader(data), "r") as archive:
            result: dict[str, bytes] = {}
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if not name.startswith(KIT_PREFIX):
                    raise BootstrapBuildError(f"unexpected bootstrap-kit entry outside {KIT_PREFIX}: {name}")
                rel = name[len(KIT_PREFIX):]
                if not rel:
                    continue
                result[rel] = archive.read(info)
            return result
    except zipfile.BadZipFile as exc:
        raise BootstrapBuildError(f"invalid bootstrap kit: {exc}") from exc


class _BytesReader:
    """Small seekable bytes wrapper accepted by zipfile without importing io globally."""

    def __init__(self, data: bytes) -> None:
        import io
        self._inner = io.BytesIO(data)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self._inner.close()


def _infer_old_version(entries: Mapping[str, bytes], requested: str) -> str:
    candidates: set[str] = set()
    for rel in WRAPPER_REQUIRED:
        raw = entries.get(rel)
        if raw is None:
            raise BootstrapBuildError(f"bootstrap kit is missing wrapper template: {rel}")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BootstrapBuildError(f"bootstrap wrapper is not UTF-8: {rel}") from exc
        candidates.update(m.group(0) for m in SEMVERISH_RE.finditer(text))
    if requested in candidates and len(candidates) == 1:
        return requested
    oldish = sorted(value for value in candidates if value != requested)
    if len(oldish) == 1:
        return oldish[0]
    if not oldish and requested in candidates:
        return requested
    raise BootstrapBuildError(
        "cannot infer one previous bootstrap release version from wrapper files; "
        f"found {sorted(candidates)!r}. Use a clean previously generated kit."
    )


def _replace_version(raw: bytes, old: str, new: str, *, rel: str) -> bytes:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BootstrapBuildError(f"bootstrap wrapper is not UTF-8: {rel}") from exc
    if old != new:
        text = text.replace(old, new)
    return text.encode("utf-8")


def _replace_exact_metadata_values(value: Any, *, old_version: str, new_version: str, old_fp: str, new_fp: str, old_count: int, new_count: int, key: str = "") -> Any:
    if isinstance(value, dict):
        return {
            k: _replace_exact_metadata_values(
                v,
                old_version=old_version,
                new_version=new_version,
                old_fp=old_fp,
                new_fp=new_fp,
                old_count=old_count,
                new_count=new_count,
                key=str(k),
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            _replace_exact_metadata_values(
                item,
                old_version=old_version,
                new_version=new_version,
                old_fp=old_fp,
                new_fp=new_fp,
                old_count=old_count,
                new_count=new_count,
                key=key,
            )
            for item in value
        ]
    lower = key.lower()
    if isinstance(value, str):
        if value == old_version:
            return new_version
        if value == old_fp and ("fingerprint" in lower or "hash" in lower or "sha" in lower):
            return new_fp
        return value.replace(old_version, new_version) if old_version != new_version else value
    if isinstance(value, int) and value == old_count and ("files" in lower or "count" in lower):
        return new_count
    return value


def _manifest_bytes(entries: Mapping[str, bytes]) -> bytes:
    lines = ["# agent-devtools-bootstrap-kit-manifest-v1"]
    for rel in sorted(entries):
        if rel == MANIFEST_REL:
            continue
        lines.append(f"{sha256_bytes(entries[rel])}  {rel}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _deterministic_zip(entries: Mapping[str, bytes]) -> bytes:
    import io
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for rel in sorted(entries):
            info = zipfile.ZipInfo(KIT_PREFIX + rel, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            archive.writestr(info, entries[rel], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return out.getvalue()


def _wrap_b85(data: bytes, width: int = 120) -> str:
    encoded = base64.b85encode(data).decode("ascii")
    return "\n".join(encoded[i:i + width] for i in range(0, len(encoded), width))


def _single_file_installer(version: str, kit_bytes: bytes) -> bytes:
    kit_sha = sha256_bytes(kit_bytes)
    kit_name = f"agent-devtools-integration-update-{version}-minimal.zip"
    b85 = _wrap_b85(kit_bytes)
    text = f'''#!/usr/bin/env python3
"""AGENT: execute this file from the intended workspace root. No separate install prompt is required."""
from __future__ import annotations
import argparse,base64,hashlib,json,subprocess,sys,tempfile,zipfile
from pathlib import Path
INSTALLER_FORMAT="agent-devtools-single-file-installer"
INSTALLER_VERSION=10
RELEASE_VERSION={version!r}
PROJECT_AUTHOR="Nikolay Laptev"
PROJECT_CONTACT="caveboy@yandex.ru"
PROJECT_GITHUB="Utundry"
EMBEDDED_KIT_FILENAME={kit_name!r}
EMBEDDED_KIT_SHA256={kit_sha!r}
_EMBEDDED_B85=r"""
{b85}
"""
def _payload():
 d=base64.b85decode("".join(_EMBEDDED_B85.split()).encode()); got=hashlib.sha256(d).hexdigest()
 if got!=EMBEDDED_KIT_SHA256: raise SystemExit(f"embedded kit hash mismatch: {{got}} != {{EMBEDDED_KIT_SHA256}}")
 return d
def main(argv=None):
 ap=argparse.ArgumentParser(description={f"Agent DevTools {version} universal-first installer/update"!r})
 ap.add_argument("--self-check",action="store_true"); ap.add_argument("--expect-version"); ap.add_argument("--target"); ap.add_argument("--preset"); ap.add_argument("--profile",choices=["development","research","analysis","document","general"]); ap.add_argument("--intent"); ap.add_argument("--stack"); ap.add_argument("--plan",action="store_true"); ap.add_argument("--json",action="store_true",dest="json_output")
 args=ap.parse_args(argv); data=_payload()
 if args.expect_version and args.expect_version!=RELEASE_VERSION:
  p={{"format":INSTALLER_FORMAT,"formatVersion":INSTALLER_VERSION,"status":"fail","releaseVersion":RELEASE_VERSION,"expectedVersion":args.expect_version,"error":"installer release version mismatch"}}
  print(json.dumps(p,ensure_ascii=False,indent=2) if args.json_output else f"FAIL: installer release {{RELEASE_VERSION}} != expected {{args.expect_version}}")
  return 2
 if args.self_check:
  p={{"format":INSTALLER_FORMAT,"formatVersion":INSTALLER_VERSION,"status":"pass","releaseVersion":RELEASE_VERSION,"embeddedKit":EMBEDDED_KIT_FILENAME,"embeddedKitSha256":EMBEDDED_KIT_SHA256,"embeddedBytes":len(data),"author":PROJECT_AUTHOR,"contact":PROJECT_CONTACT,"github":PROJECT_GITHUB}}; print(json.dumps(p,ensure_ascii=False,indent=2) if args.json_output else f"PASS: Agent DevTools {{RELEASE_VERSION}} · {{EMBEDDED_KIT_FILENAME}} · {{EMBEDDED_KIT_SHA256}}"); return 0
 with tempfile.TemporaryDirectory(prefix="agent-devtools-bootstrap-") as td:
  r=Path(td); a=r/EMBEDDED_KIT_FILENAME; a.write_bytes(data)
  with zipfile.ZipFile(a) as z: z.extractall(r/'kit')
  launcher=r/'kit'/'.agent-devtools-bootstrap-kit'/'BOOTSTRAP_AGENT_DEVTOOLS.py'; cmd=[sys.executable,str(launcher)]
  if args.target: cmd += ['--target',args.target]
  if args.preset: cmd += ['--preset',args.preset]
  if args.profile: cmd += ['--profile',args.profile]
  if args.intent: cmd += ['--intent',args.intent]
  if args.stack: cmd += ['--stack',args.stack]
  if args.plan: cmd += ['--plan']
  if args.json_output: cmd += ['--json']
  return subprocess.run(cmd).returncode
if __name__=="__main__": raise SystemExit(main())
'''
    return text.encode("utf-8")


def build_artifacts(root: Path, *, version: str, template_kit: bytes, source_version: str | None = None) -> BuildResult:
    root = root.resolve()
    source_version = source_version or _read_source_version(root)
    if source_version != version:
        raise BootstrapBuildError(
            f"source version mismatch: agent_devtools.__version__={source_version!r}, requested bootstrap={version!r}; "
            "bump source version first"
        )

    old_entries = _read_kit_entries(template_kit)
    old_payload = {
        rel[len("payload/"):]: data
        for rel, data in old_entries.items()
        if rel.startswith("payload/")
    }
    new_payload = _payload_inventory(root)
    old_version = _infer_old_version(old_entries, version)
    old_fp = _tree_fingerprint(old_payload)
    new_fp = _tree_fingerprint(new_payload)

    entries: dict[str, bytes] = {}
    for rel in WRAPPER_REQUIRED:
        raw = old_entries[rel]
        entries[rel] = _replace_version(raw, old_version, version, rel=rel)

    # Refresh exact known metadata values without assuming a particular metadata schema.
    try:
        metadata = json.loads(entries["bootstrap-kit.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapBuildError(f"invalid bootstrap-kit.json: {exc}") from exc
    metadata = _replace_exact_metadata_values(
        metadata,
        old_version=old_version,
        new_version=version,
        old_fp=old_fp,
        new_fp=new_fp,
        old_count=len(old_payload),
        new_count=len(new_payload),
    )
    entries["bootstrap-kit.json"] = (json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")

    for rel, data in new_payload.items():
        entries["payload/" + rel] = data
    entries[MANIFEST_REL] = _manifest_bytes(entries)
    kit_bytes = _deterministic_zip(entries)
    installer_bytes = _single_file_installer(version, kit_bytes)

    old_set = set(old_payload)
    new_set = set(new_payload)
    return BuildResult(
        version=version,
        source_version=source_version,
        source_head=_git_head(root),
        payload_files=len(new_payload),
        payload_fingerprint=new_fp,
        kit_bytes=kit_bytes,
        kit_sha256=sha256_bytes(kit_bytes),
        installer_bytes=installer_bytes,
        installer_sha256=sha256_bytes(installer_bytes),
        old_version=old_version,
        added_payload=tuple(sorted(new_set - old_set)),
        removed_payload=tuple(sorted(old_set - new_set)),
    )


def _embedded_kit(installer: bytes) -> tuple[str, str, bytes]:
    text = installer.decode("utf-8")
    name_match = re.search(r'^EMBEDDED_KIT_FILENAME=(.+)$', text, re.MULTILINE)
    sha_match = re.search(r'^EMBEDDED_KIT_SHA256=(.+)$', text, re.MULTILINE)
    data_match = re.search(r'_EMBEDDED_B85=r"""\n([\s\S]*?)\n"""', text)
    if not name_match or not sha_match or not data_match:
        raise BootstrapBuildError("generated single-file installer is malformed")
    name = json.loads(name_match.group(1).replace("'", '"')) if name_match.group(1).startswith("'") else json.loads(name_match.group(1))
    expected = json.loads(sha_match.group(1).replace("'", '"')) if sha_match.group(1).startswith("'") else json.loads(sha_match.group(1))
    payload = base64.b85decode("".join(data_match.group(1).split()).encode("ascii"))
    return str(name), str(expected), payload


def verify_generated(result: BuildResult) -> None:
    name, expected_sha, embedded = _embedded_kit(result.installer_bytes)
    if name != f"agent-devtools-integration-update-{result.version}-minimal.zip":
        raise BootstrapBuildError(f"unexpected embedded kit filename: {name}")
    installer_text = result.installer_bytes.decode("utf-8")
    if f"RELEASE_VERSION={result.version!r}" not in installer_text:
        raise BootstrapBuildError("single-file installer records the wrong release version")
    if expected_sha != result.kit_sha256:
        raise BootstrapBuildError("single-file installer records the wrong kit sha256")
    if embedded != result.kit_bytes:
        raise BootstrapBuildError("single-file installer payload differs from generated kit bytes")
    if sha256_bytes(embedded) != expected_sha:
        raise BootstrapBuildError("embedded kit hash verification failed")

    entries = _read_kit_entries(result.kit_bytes)
    manifest = entries.get(MANIFEST_REL)
    if manifest is None:
        raise BootstrapBuildError("generated kit has no MANIFEST.sha256")
    failures: list[str] = []
    for line in manifest.decode("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            expected, rel = line.split("  ", 1)
        except ValueError:
            failures.append(f"malformed manifest line: {line}")
            continue
        raw = entries.get(rel)
        if raw is None:
            failures.append(f"manifest missing entry: {rel}")
        elif sha256_bytes(raw) != expected:
            failures.append(f"manifest hash mismatch: {rel}")
    if failures:
        raise BootstrapBuildError("; ".join(failures))


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(data)
    os.replace(temp, path)


def _payload(root: Path, result: BuildResult, *, status: str) -> dict[str, Any]:
    return {
        "format": "agent-devtools-bootstrap-build",
        "formatVersion": 1,
        "status": status,
        "version": result.version,
        "sourceVersion": result.source_version,
        "sourceHead": result.source_head,
        "payloadFiles": result.payload_files,
        "payloadFingerprint": result.payload_fingerprint,
        "templateVersion": result.old_version,
        "payloadAdded": list(result.added_payload),
        "payloadRemoved": list(result.removed_payload),
        "kit": {"path": str(DEFAULT_KIT.relative_to(root)), "sha256": result.kit_sha256, "bytes": len(result.kit_bytes)},
        "installer": {"path": str(DEFAULT_INSTALLER.relative_to(root)), "sha256": result.installer_sha256, "bytes": len(result.installer_bytes)},
    }


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Rebuild Agent DevTools bootstrap kit and single-file installer from the exact current source tree")
    ap.add_argument("--version", required=True, help="public bootstrap/release version; must equal agent_devtools.__version__")
    ap.add_argument("--check", action="store_true", help="do not write; fail if committed bootstrap artifacts differ from a fresh rebuild")
    ap.add_argument("--allow-dirty", action="store_true", help="allow an intentionally dirty Git tree (not recommended for release builds)")
    ap.add_argument("--json", action="store_true", dest="json_output")
    return ap


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        if not args.allow_dirty:
            allowed_dirty = ()
            if args.check:
                # A normal build intentionally changes these two derived artifacts.
                # --check must be able to validate that exact post-build state while
                # still refusing every source/config/test change.
                allowed_dirty = (
                    DEFAULT_KIT.relative_to(ROOT).as_posix(),
                    DEFAULT_INSTALLER.relative_to(ROOT).as_posix(),
                )
            _assert_clean_git(ROOT, allowed_paths=allowed_dirty)
        if not DEFAULT_KIT.is_file():
            raise BootstrapBuildError(f"bootstrap template kit is missing: {DEFAULT_KIT}")
        template = DEFAULT_KIT.read_bytes()
        result = build_artifacts(ROOT, version=args.version, template_kit=template)
        verify_generated(result)
        if args.check:
            failures: list[str] = []
            if DEFAULT_KIT.read_bytes() != result.kit_bytes:
                failures.append("bootstrap/agent-devtools-bootstrap-kit.zip is stale")
            if not DEFAULT_INSTALLER.is_file() or DEFAULT_INSTALLER.read_bytes() != result.installer_bytes:
                failures.append("bootstrap/AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py is stale")
            if failures:
                raise BootstrapBuildError("; ".join(failures))
            status = "pass"
        else:
            _write_atomic(DEFAULT_KIT, result.kit_bytes)
            _write_atomic(DEFAULT_INSTALLER, result.installer_bytes)
            # Prove idempotence using the just-written kit as the next template.
            repeated = build_artifacts(ROOT, version=args.version, template_kit=result.kit_bytes)
            verify_generated(repeated)
            if repeated.kit_bytes != result.kit_bytes or repeated.installer_bytes != result.installer_bytes:
                raise BootstrapBuildError("bootstrap rebuild is not idempotent")
            status = "built"
        payload = _payload(ROOT, result, status=status)
    except (BootstrapBuildError, OSError) as exc:
        if args.json_output:
            print(json.dumps({"format": "agent-devtools-bootstrap-build", "formatVersion": 1, "status": "fail", "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"{status.upper()}: Agent DevTools bootstrap {result.version}")
        print(f"  source: {result.source_head or 'no-git'} · {result.payload_files} runtime file(s) · {result.payload_fingerprint}")
        print(f"  kit: {result.kit_sha256} · {len(result.kit_bytes)} bytes")
        print(f"  installer: {result.installer_sha256} · {len(result.installer_bytes)} bytes")
        if result.added_payload:
            print("  payload added: " + ", ".join(result.added_payload))
        if result.removed_payload:
            print("  payload removed: " + ", ".join(result.removed_payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
