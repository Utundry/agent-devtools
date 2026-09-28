from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .changes import (
    ChangeSetError,
    build_patch,
    discover_changes,
    mark_workspace_local,
    unmark_workspace_local,
    workspace_local_status,
)


def configure_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="changes_command", required=True)
    status = sub.add_parser("status", help="show the canonical project change-set, including untracked source/knowledge files")
    status.add_argument("--config", type=Path, default=None)
    status.add_argument("--json", action="store_true", dest="json_output")
    patch = sub.add_parser("patch", help="write a Git-applicable patch including canonical untracked files without staging them")
    patch.add_argument("--config", type=Path, default=None)
    patch.add_argument("--out", type=Path, required=True)
    patch.add_argument("--json", action="store_true", dest="json_output")

    local = sub.add_parser("local", help="manage content-addressed workspace-local change marks")
    local_sub = local.add_subparsers(dest="changes_local_command", required=True)
    local_mark = local_sub.add_parser("mark", help="mark current file bytes as workspace-local")
    local_mark.add_argument("paths", nargs="+")
    local_mark.add_argument("--reason", default="")
    local_mark.add_argument("--json", action="store_true", dest="json_output")
    local_list = local_sub.add_parser("list", help="show workspace-local marks and whether they still match")
    local_list.add_argument("--json", action="store_true", dest="json_output")
    local_clear = local_sub.add_parser("clear", help="remove workspace-local marks")
    local_clear.add_argument("paths", nargs="+")
    local_clear.add_argument("--json", action="store_true", dest="json_output")


def main(root: Path, args: argparse.Namespace) -> int:
    try:
        if args.changes_command == "local":
            if args.changes_local_command == "mark":
                report = mark_workspace_local(root, args.paths, reason=args.reason)
            elif args.changes_local_command == "clear":
                report = unmark_workspace_local(root, args.paths)
            else:
                report = workspace_local_status(root)
        elif args.changes_command == "status":
            report = discover_changes(root, args.config)
        else:
            payload, report = build_patch(root, args.config)
            out = args.out if args.out.is_absolute() else root / args.out
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(payload)
            report = dict(report)
            report["patch"] = str(out)
    except ChangeSetError as exc:
        print(f"agent changes: {exc}", file=sys.stderr)
        return 2
    if args.json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        if args.changes_command == "local":
            if args.changes_local_command == "mark":
                print("workspace-local marked: " + ", ".join(report["marked"]))
            elif args.changes_local_command == "clear":
                print("workspace-local cleared: " + (", ".join(report["removed"]) or "none"))
            else:
                print(f"workspace-local: {report['matching']}/{report['count']} exact marks")
                for item in report["marks"]:
                    marker = "match" if item["matches"] else "MISMATCH"
                    suffix = f" · {item['reason']}" if item.get("reason") else ""
                    print(f"  {marker}: {item['path']}{suffix}")
        else:
            if not report.get("available"):
                print(f"changes: unavailable · {report.get('reason')}")
                return 2
            print(
                f"changes: {len(report.get('canonicalChangedFiles', []))} canonical · "
                f"{report.get('canonicalUntrackedCount', 0)} untracked canonical · "
                f"{report.get('workspaceLocalChangedCount', 0)} workspace-local · "
                f"{report.get('knowledgeChangedCount', 0)} knowledge"
            )
            for rel in report.get("workspaceLocalMarkMismatches", []):
                print(f"  local mark mismatch -> canonical: {rel}")
            if args.changes_command == "patch":
                print(f"patch: {report['patch']} · {report['patchBytes']} bytes")
    return 0
