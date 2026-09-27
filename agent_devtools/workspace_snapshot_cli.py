from __future__ import annotations
import argparse, json
from pathlib import Path
from .workspace_snapshot import WorkspaceSnapshotError, create_snapshot, inspect_snapshot, restore_snapshot

def configure_parser(p: argparse.ArgumentParser) -> None:
    sub=p.add_subparsers(dest="snapshot_command",required=True)
    c=sub.add_parser("create",help="create portable non-development workspace snapshot")
    c.add_argument("--out",type=Path); c.add_argument("--json",action="store_true",dest="json_output")
    i=sub.add_parser("inspect",help="verify and inspect a workspace snapshot")
    i.add_argument("path",type=Path); i.add_argument("--json",action="store_true",dest="json_output")
    r=sub.add_parser("restore",help="restore workspace artifacts and normalized work state")
    r.add_argument("path",type=Path); r.add_argument("--target",type=Path,default=Path.cwd()); r.add_argument("--force",action="store_true"); r.add_argument("--json",action="store_true",dest="json_output")

def main(root: Path,args: argparse.Namespace)->int:
    try:
        if args.snapshot_command=="create": payload=create_snapshot(root,out=args.out)
        elif args.snapshot_command=="inspect": payload=inspect_snapshot(args.path)
        else: payload=restore_snapshot(args.path,args.target,force=bool(args.force))
    except WorkspaceSnapshotError as exc:
        print(f"agent workspace snapshot: {exc}",file=__import__('sys').stderr); return 2
    if getattr(args,"json_output",False): print(json.dumps(payload,ensure_ascii=False,indent=2))
    else:
        if args.snapshot_command=="create": print(f"snapshot: {payload['path']} · {payload['artifacts']} artifacts · {payload['sha256']}")
        elif args.snapshot_command=="inspect": print(f"snapshot: PASS · profile={payload['workspace']['profile']} · artifacts={len(payload['artifacts'])} · corpus={payload['corpusFingerprint']}")
        else: print(f"snapshot restore: PASS · {payload['restoredArtifacts']} artifacts -> {payload['target']}")
    return 0
