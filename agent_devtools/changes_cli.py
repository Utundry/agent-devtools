from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .changes import ChangeSetError, build_patch, discover_changes


def configure_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="changes_command", required=True)
    status = sub.add_parser("status", help="show the canonical project change-set, including untracked source/knowledge files")
    status.add_argument("--config", type=Path, default=None)
    status.add_argument("--json", action="store_true", dest="json_output")
    patch = sub.add_parser("patch", help="write a Git-applicable patch including canonical untracked files without staging them")
    patch.add_argument("--config", type=Path, default=None)
    patch.add_argument("--out", type=Path, required=True)
    patch.add_argument("--json", action="store_true", dest="json_output")


def main(root: Path, args: argparse.Namespace) -> int:
    try:
        if args.changes_command == "status":
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
        if not report.get("available"):
            print(f"changes: unavailable · {report.get('reason')}")
            return 2
        print(f"changes: {len(report.get('canonicalChangedFiles', []))} canonical · {report.get('canonicalUntrackedCount', 0)} untracked canonical · {report.get('knowledgeChangedCount', 0)} knowledge")
        if args.changes_command == "patch":
            print(f"patch: {report['patch']} · {report['patchBytes']} bytes")
    return 0
