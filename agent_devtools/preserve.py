from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from typing import Any, Iterable

from agent_devtools.profiles import ProfileError, load_profile
from agent_devtools.work.checkpoint import CheckpointError, create_checkpoint, inspect_checkpoint, restore_checkpoint
from agent_devtools.workspace_snapshot import WorkspaceSnapshotError, create_snapshot, inspect_snapshot, restore_snapshot


class PreserveError(RuntimeError):
    pass


def _kind_from_archive(path: Path) -> str:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise PreserveError(f"preservation artifact not found: {path}")
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise PreserveError(f"cannot read preservation artifact: {exc}") from exc
    checkpoint = "checkpoint.json" in names
    snapshot = "snapshot.json" in names
    if checkpoint == snapshot:
        raise PreserveError(
            "cannot determine preservation artifact kind; expected exactly one of checkpoint.json or snapshot.json"
        )
    return "checkpoint" if checkpoint else "workspace-snapshot"


def create_preservation(
    root: Path,
    *,
    out: Path | None = None,
    include: Iterable[str] = (),
    max_bytes: int | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    try:
        profile = load_profile(root)
    except ProfileError as exc:
        raise PreserveError(str(exc)) from exc

    try:
        if profile.development:
            payload = create_checkpoint(
                root,
                out=out,
                include=include,
                max_bytes=max_bytes if max_bytes is not None else 100 * 1024 * 1024,
            )
            kind = "checkpoint"
        else:
            explicit = [str(item).strip() for item in include if str(item).strip()]
            if explicit:
                raise PreserveError(
                    "--include is development-checkpoint specific; non-development snapshots preserve the workspace corpus automatically"
                )
            payload = create_snapshot(
                root,
                out=out,
                max_bytes=max_bytes if max_bytes is not None else 250 * 1024 * 1024,
            )
            kind = "workspace-snapshot"
    except (CheckpointError, WorkspaceSnapshotError) as exc:
        raise PreserveError(str(exc)) from exc

    return {
        "format": "agent-devtools-preservation",
        "formatVersion": 1,
        "action": "create",
        "kind": kind,
        "profile": profile.profile_id,
        "development": profile.development,
        "artifact": payload,
    }


def inspect_preservation(path: Path) -> dict[str, Any]:
    kind = _kind_from_archive(path)
    try:
        payload = inspect_checkpoint(path) if kind == "checkpoint" else inspect_snapshot(path)
    except (CheckpointError, WorkspaceSnapshotError) as exc:
        raise PreserveError(str(exc)) from exc
    return {
        "format": "agent-devtools-preservation",
        "formatVersion": 1,
        "action": "inspect",
        "kind": kind,
        "artifact": payload,
    }


def restore_preservation(
    root: Path,
    path: Path,
    *,
    target: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    kind = _kind_from_archive(path)
    destination = (target or root).expanduser().resolve()
    try:
        payload = (
            restore_checkpoint(destination, path, force=force)
            if kind == "checkpoint"
            else restore_snapshot(path, destination, force=force)
        )
    except (CheckpointError, WorkspaceSnapshotError) as exc:
        raise PreserveError(str(exc)) from exc
    return {
        "format": "agent-devtools-preservation",
        "formatVersion": 1,
        "action": "restore",
        "kind": kind,
        "target": str(destination),
        "artifact": payload,
    }


def configure_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="preserve_command", required=True)

    create = sub.add_parser("create", help="create the profile-appropriate portable preservation artifact")
    create.add_argument("--out", type=Path, default=None)
    create.add_argument("--include", action="append", default=[], help="development only: explicit project-relative file to include")
    create.add_argument("--max-bytes", type=int, default=None, help="override the profile-appropriate artifact size limit")
    create.add_argument("--json", action="store_true", dest="json_output")

    inspect = sub.add_parser("inspect", help="auto-detect, verify, and inspect a checkpoint or workspace snapshot")
    inspect.add_argument("path", type=Path)
    inspect.add_argument("--json", action="store_true", dest="json_output")

    restore = sub.add_parser("restore", help="auto-detect and restore a checkpoint or workspace snapshot")
    restore.add_argument("path", type=Path)
    restore.add_argument("--target", type=Path, default=None)
    restore.add_argument("--force", action="store_true")
    restore.add_argument("--json", action="store_true", dest="json_output")


def main(root: Path, args: argparse.Namespace) -> int:
    try:
        if args.preserve_command == "create":
            if args.max_bytes is not None and args.max_bytes <= 0:
                raise PreserveError("--max-bytes must be positive")
            payload = create_preservation(root, out=args.out, include=args.include, max_bytes=args.max_bytes)
        elif args.preserve_command == "inspect":
            payload = inspect_preservation(args.path)
        elif args.preserve_command == "restore":
            payload = restore_preservation(root, args.path, target=args.target, force=bool(args.force))
        else:
            return 2
    except PreserveError as exc:
        print(f"agent preserve: {exc}", file=__import__("sys").stderr)
        return 2

    if getattr(args, "json_output", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    artifact = payload["artifact"]
    kind = payload["kind"]
    if payload["action"] == "create":
        count = f"{artifact.get('files', 0)} file(s)" if kind == "checkpoint" else f"{artifact.get('artifacts', 0)} artifact(s)"
        print(f"PASS: preserve create · {kind} · {count}")
        print(f"  sha256={artifact.get('sha256')}")
        print(f"  {artifact.get('path')}")
    elif payload["action"] == "inspect":
        print(f"PASS: preserve inspect · {kind}")
        print(f"  {artifact.get('path')}")
    else:
        print(f"PASS: preserve restore · {kind} -> {payload['target']}")
    return 0
