from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_devtools.work.brief import render_brief
from .handoff import HandoffError, create_handoff, inspect_handoff, resume_handoff


def configure_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="handoff_command", required=True)

    create = sub.add_parser("create", help="create a portable cross-session/cross-agent handoff bundle")
    create.add_argument("--out", type=Path, default=None)
    create.add_argument("--include", action="append", default=[], help="development only: explicit file to include")
    create.add_argument("--max-bytes", type=int, default=None)
    create.add_argument("--budget", type=int, default=1400)
    create.add_argument("--no-context", action="store_true")
    create.add_argument("--json", action="store_true", dest="json_output")

    inspect = sub.add_parser("inspect", help="verify and inspect a handoff without restoring it")
    inspect.add_argument("path", type=Path)
    inspect.add_argument("--json", action="store_true", dest="json_output")

    resume = sub.add_parser("resume", help="restore preserved state safely and emit a fresh resume briefing")
    resume.add_argument("path", type=Path)
    resume.add_argument("--target", type=Path, default=None)
    resume.add_argument("--force", action="store_true")
    resume.add_argument("--budget", type=int, default=1400)
    resume.add_argument("--no-context", action="store_true")
    resume.add_argument("--json", action="store_true", dest="json_output")


def main(root: Path, args: argparse.Namespace) -> int:
    try:
        if args.handoff_command == "create":
            if args.max_bytes is not None and args.max_bytes <= 0:
                raise HandoffError("--max-bytes must be positive")
            payload = create_handoff(
                root,
                out=args.out,
                include=args.include,
                max_bytes=args.max_bytes,
                budget=args.budget,
                include_context=not args.no_context,
            )
        elif args.handoff_command == "inspect":
            payload = inspect_handoff(args.path)
        elif args.handoff_command == "resume":
            payload = resume_handoff(
                root,
                args.path,
                target=args.target,
                force=bool(args.force),
                budget=args.budget,
                include_context=not args.no_context,
            )
        else:
            return 2
    except HandoffError as exc:
        print(f"agent handoff: {exc}", file=__import__("sys").stderr)
        return 2

    if getattr(args, "json_output", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.handoff_command == "create":
        print(f"PASS: handoff create · {payload['kind']} · sha256={payload['sha256']}")
        print(f"  {payload['path']}")
        print(render_brief(payload["brief"]))
    elif args.handoff_command == "inspect":
        print(
            f"PASS: handoff inspect · {payload['kind']} · "
            f"profile={payload.get('profile')} · fingerprint={payload.get('workingStateFingerprint')}"
        )
        print(f"  {payload['path']}")
    else:
        print(f"PASS: handoff resume · {payload['kind']} -> {payload['target']}")
        print(payload["briefText"])
    return 0
