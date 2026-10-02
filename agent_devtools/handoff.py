from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import shutil
from contextlib import contextmanager
from agent_devtools.core.hashing import sha256_file
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from agent_devtools import __version__
from agent_devtools.core.archive import ArchiveSafetyError, verified_zip, read_metadata
from agent_devtools.core.workspace import default_work_root
from agent_devtools.preserve import (
    PreserveError,
    create_preservation,
    inspect_preservation,
    restore_preservation,
)
from agent_devtools.work.brief import build_brief, render_brief

HANDOFF_FORMAT = "agent-devtools-handoff"
HANDOFF_VERSION = 1


class HandoffError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip_write(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name)
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    zf.writestr(info, data)


def _manifest(entries: dict[str, str]) -> bytes:
    return "".join(
        f"{digest}  {name}\n" for name, digest in sorted(entries.items())
    ).encode("utf-8")


def _default_path(root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return default_work_root(root) / "handoffs" / f"handoff-{stamp}.agent-handoff.zip"


def create_handoff(
    root: Path,
    *,
    out: Path | None = None,
    include: Iterable[str] = (),
    max_bytes: int | None = None,
    budget: int = 1400,
    include_context: bool = True,
) -> dict[str, Any]:
    root = root.resolve()
    if budget < 128:
        raise HandoffError("--budget must be >= 128")

    brief = build_brief(root, mode="resume", budget=budget, include_context=include_context)
    preservation_temp = tempfile.TemporaryDirectory(prefix="agent-devtools-handoff-")
    preservation_path = Path(preservation_temp.name) / "preservation.zip"
    try:
        preservation = create_preservation(root, out=preservation_path, include=include, max_bytes=max_bytes)
    except PreserveError as exc:
        preservation_temp.cleanup()
        raise HandoffError(str(exc)) from exc

    handoff = {
        "format": HANDOFF_FORMAT,
        "formatVersion": HANDOFF_VERSION,
        "toolVersion": __version__,
        "createdAtUtc": _utc_now(),
        "project": {
            "rootName": root.name,
            "profile": (
                (brief.get("profile") or {}).get("id")
                if isinstance(brief.get("profile"), dict)
                else None
            ),
        },
        "workingStateFingerprint": brief.get("workingStateFingerprint"),
        "preservationKind": preservation.get("kind"),
        "preservationSha256": sha256_file(preservation_path),
        "briefIncluded": True,
        "preservationIncluded": True,
    }

    payloads = {
        "handoff.json": (
            json.dumps(handoff, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8"),
        "brief.json": (
            json.dumps(brief, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8"),
        "brief.txt": (render_brief(brief) + "\n").encode("utf-8"),
        "HANDOFF.txt": (
            "AGENT DEVTOOLS HANDOFF\n\n"
            "This bundle contains a profile-aware preservation artifact and a resume briefing.\n"
            "With Agent DevTools installed in the target workspace, run:\n\n"
            "  python devtools/agent/agent.py handoff resume <this-file>\n"
        ).encode("utf-8"),
    }
    payloads["MANIFEST.sha256"] = _manifest(
        {**{name: _sha(data) for name, data in payloads.items()}, "preservation.zip": sha256_file(preservation_path)}
    )

    target = (out or _default_path(root)).expanduser()
    if not target.is_absolute():
        target = (root / target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    try:
        with zipfile.ZipFile(temp, "w") as zf:
            for name in sorted(payloads):
                _zip_write(zf, name, payloads[name])
            info = zipfile.ZipInfo("preservation.zip", (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            with preservation_path.open("rb") as source, zf.open(info, "w") as sink:
                shutil.copyfileobj(source, sink, length=1024 * 1024)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
        preservation_temp.cleanup()

    return {
        "status": "pass",
        "path": str(target),
        "sha256": sha256_file(target),
        "kind": preservation.get("kind"),
        "profile": handoff["project"]["profile"],
        "workingStateFingerprint": handoff["workingStateFingerprint"],
        "preservationSha256": handoff["preservationSha256"],
        "brief": brief,
    }


@contextmanager
def _read_handoff(path: Path):
    try:
        with verified_zip(path) as (archive, members, expected), tempfile.TemporaryDirectory(prefix="agent-devtools-handoff-read-") as td:
            required = {"handoff.json", "brief.json", "brief.txt", "preservation.zip", "MANIFEST.sha256"}
            missing = required - set(members)
            if missing:
                raise HandoffError("handoff is missing required entries: " + ", ".join(sorted(missing)))
            payloads = {name: read_metadata(archive, members[name]) for name in ("handoff.json", "brief.json", "brief.txt")}
            meta = json.loads(payloads["handoff.json"].decode("utf-8"))
            brief = json.loads(payloads["brief.json"].decode("utf-8"))
            if (not isinstance(meta, dict) or meta.get("format") != HANDOFF_FORMAT
                    or int(meta.get("formatVersion") or 0) != HANDOFF_VERSION):
                raise HandoffError("unsupported handoff format/version")
            if not isinstance(brief, dict):
                raise HandoffError("handoff brief must be a JSON object")
            if expected["preservation.zip"] != meta.get("preservationSha256"):
                raise HandoffError("handoff preservation hash does not match metadata")
            preserved = Path(td) / "preservation.zip"
            with archive.open(members["preservation.zip"]) as source, preserved.open("wb") as sink:
                shutil.copyfileobj(source, sink, length=1024 * 1024)
            yield meta, payloads, preserved
    except (ArchiveSafetyError, ValueError, UnicodeDecodeError) as exc:
        raise HandoffError(str(exc)) from exc


def inspect_handoff(path: Path) -> dict[str, Any]:
    with _read_handoff(path) as (meta, payloads, preserved):
        try:
            preservation = inspect_preservation(preserved)
        except PreserveError as exc:
            raise HandoffError(str(exc)) from exc

    return {
        "status": "pass",
        "path": str(path.expanduser().resolve()),
        "sha256": sha256_file(path.expanduser().resolve()),
        "createdAtUtc": meta.get("createdAtUtc"),
        "toolVersion": meta.get("toolVersion"),
        "profile": (
            (meta.get("project") or {}).get("profile")
            if isinstance(meta.get("project"), dict)
            else None
        ),
        "kind": meta.get("preservationKind"),
        "workingStateFingerprint": meta.get("workingStateFingerprint"),
        "preservation": preservation,
        "brief": json.loads(payloads["brief.json"].decode("utf-8")),
    }


def resume_handoff(
    root: Path,
    path: Path,
    *,
    target: Path | None = None,
    force: bool = False,
    budget: int = 1400,
    include_context: bool = True,
) -> dict[str, Any]:
    if budget < 128:
        raise HandoffError("--budget must be >= 128")
    destination = (target or root).expanduser().resolve()
    with _read_handoff(path) as (meta, payloads, preserved):
        try:
            restored = restore_preservation(destination, preserved, target=destination, force=force)
        except PreserveError as exc:
            raise HandoffError(str(exc)) from exc

    fresh = build_brief(
        destination,
        mode="resume",
        budget=budget,
        include_context=include_context,
    )
    return {
        "status": "pass",
        "path": str(path.expanduser().resolve()),
        "target": str(destination),
        "kind": meta.get("preservationKind"),
        "restored": restored,
        "originalWorkingStateFingerprint": meta.get("workingStateFingerprint"),
        "currentWorkingStateFingerprint": fresh.get("workingStateFingerprint"),
        "brief": fresh,
        "briefText": render_brief(fresh),
    }
