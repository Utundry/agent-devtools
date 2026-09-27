from __future__ import annotations

import argparse
import json
from pathlib import Path

from .build import ReleaseBuildError, build_release, verify_release


def configure_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="release_command", required=True)
    build = sub.add_parser("build", help="certify/replay and create deterministic release artifacts")
    build.add_argument("--version", required=True)
    build.add_argument("--base", type=Path, required=True)
    build.add_argument("--out-dir", type=Path, required=True)
    build.add_argument("--config", type=Path, default=None)
    build.add_argument("--json", action="store_true", dest="json_output")
    verify = sub.add_parser("verify", help="verify package/replay hashes from RELEASE-EVIDENCE.json")
    verify.add_argument("out_dir", type=Path)
    verify.add_argument("--json", action="store_true", dest="json_output")


def main(root: Path, args: argparse.Namespace) -> int:
    try:
        if args.release_command == "build":
            code, report = build_release(
                root=root,
                version=args.version,
                base=args.base,
                out_dir=args.out_dir,
                config_path=args.config,
            )
            if args.json_output:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(f"{str(report.get('status') or 'unknown').upper()}: release {args.version}")
                for row in report.get("packages") or []:
                    print(f"  {row['id']}: {row['files']} files · {row['archive']} · sha256={row['archiveSha256']}")
                replay = report.get("replay") or {}
                if replay.get("artifact"):
                    print(f"  replay: {replay['artifact']} · sha256={replay.get('sha256')}")
            return code
        if args.release_command == "verify":
            code, report = verify_release(args.out_dir)
            if args.json_output:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(f"{report['status'].upper()}: release verify · {report.get('packages', 0)} package(s)")
                for failure in report.get("failures") or []:
                    print(f"  {failure}")
            return code
    except ReleaseBuildError as exc:
        print(f"agent release: {exc}")
        return 2
    raise AssertionError(args.release_command)
