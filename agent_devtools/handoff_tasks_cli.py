"""Optional task-oriented handoff commands (legacy ZIP handoff remains intact)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from . import handoff_tasks as tasks


def configure(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="task_handoff_command", required=True)
    make = sub.add_parser("new", help="write a Git-ignored, unclaimed task handoff")
    make.add_argument("--goal", required=True)
    make.add_argument("--kind", choices=("continuation", "delegation"), default="continuation")
    make.add_argument("--step", action="append", default=[])
    make.add_argument("--owner", default="")
    make.add_argument("--ttl-days", type=int)
    make.add_argument("--return-mode", choices=("user-mediated", "direct"))
    sub.add_parser("list", help="discover handoffs without claiming them")
    show = sub.add_parser("show", help="inspect handoff without claiming")
    show.add_argument("id")
    take = sub.add_parser("claim", help="explicitly claim an available task")
    take.add_argument("id")
    take.add_argument("--actor", required=True)
    done = sub.add_parser("result", help="return a result and portable receipt")
    done.add_argument("id")
    done.add_argument("--claim-id", required=True)
    done.add_argument("--outcome", choices=("pass", "fail", "blocked"), required=True)
    done.add_argument("--summary", required=True)
    done.add_argument("--receipt-out", type=Path)
    shut = sub.add_parser("close", help="explicitly remove local handoff after completion")
    shut.add_argument("id")
    shut.add_argument("--claim-id")
    shut.add_argument("--receipt-sha")
    shut.add_argument("--yes", action="store_true")
    export = sub.add_parser("export-task", help="write portable immutable assignment")
    export.add_argument("id")
    export.add_argument("--out", type=Path, required=True)
    importing = sub.add_parser("import-task", help="register transferred task without claim")
    importing.add_argument("file", type=Path)
    receiving = sub.add_parser("receive", help="verify a returned result without closing")
    receiving.add_argument("file", type=Path)
    ignore = sub.add_parser("ignore", help="leave a discovered handoff unchanged")
    ignore.add_argument("id")
    discard = sub.add_parser("delete", help="explicitly remove an unclaimed local handoff")
    discard.add_argument("id")
    discard.add_argument("--yes", action="store_true")
    sub.add_parser("cleanup", help="remove only expired unclaimed handoffs")


def main(root: Path, args: argparse.Namespace) -> int:
    try:
        command = args.task_handoff_command
        if command == "new":
            value = tasks.create(root, goal=args.goal, kind=args.kind, steps=args.step,
                                 owner=args.owner, ttl_days=args.ttl_days, return_mode=args.return_mode)
        elif command == "list":
            value = tasks.listing(root)
        elif command == "show":
            value = tasks.load(root, args.id)
            value = {**value, "health": tasks.flags(value)}
        elif command == "claim":
            value = tasks.claim(root, args.id, args.actor)
        elif command == "result":
            value = tasks.complete(root, args.id, claim_id=args.claim_id,
                                   outcome=args.outcome, summary=args.summary, receipt_out=args.receipt_out)
        elif command == "close":
            value = tasks.close(root, args.id, claim_id=args.claim_id,
                                receipt_sha=args.receipt_sha, confirm=args.yes)
        elif command == "export-task":
            value = tasks.export_assignment(root, args.id, args.out)
        elif command == "import-task":
            value = tasks.import_assignment(root, args.file)
        elif command == "receive":
            value = tasks.receive_result(root, args.file)
        elif command == "ignore":
            tasks.load(root, args.id)
            value = {"status": "ignored", "id": args.id, "changed": False}
        elif command == "delete":
            value = tasks.discard(root, args.id, confirm=args.yes)
        elif command == "cleanup":
            value = tasks.cleanup(root)
        else:
            return 2
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except (tasks.TaskHandoffError, OSError, ValueError) as exc:
        print(f"agent handoff tasks: {exc}", file=sys.stderr)
        return 2
