from __future__ import annotations

import argparse
import cmd
import json
import shlex
from pathlib import Path
from typing import Callable

Dispatcher = Callable[[list[str] | None], int]

_CONTEXT_SUBCOMMANDS = {
    "ensure", "rebuild", "validate", "query", "inspect", "stats", "affected", "diff",
    "prepare", "current", "why", "expand",
}
_COGNITION_ALIASES = {
    "decision": "decision",
    "finding": "finding",
    "assumption": "assumption",
    "requirement": "requirement",
    "question": "open-question",
    "open-question": "open-question",
    "evidence": "evidence",
    "observation": "observation",
    "blocker": "blocker",
    "resolve-blocker": "resolve-blocker",
}


class ShellCommandError(ValueError):
    pass


def normalize_command(line: str) -> list[str]:
    """Translate shell conveniences into the ordinary Agent DevTools CLI argv contract."""
    try:
        tokens = shlex.split(str(line or ""), posix=True)
    except ValueError as exc:
        raise ShellCommandError(str(exc)) from exc
    if not tokens:
        return []
    name, rest = tokens[0], tokens[1:]
    if name in {"exit", "quit"}:
        return []
    if name == "begin":
        if not rest or rest[0].startswith("-"):
            return ["begin", *rest]
        return ["begin", "--goal", " ".join(rest)]
    if name == "status":
        return ["work", "status", *rest]
    if name == "context":
        if not rest:
            return ["context", "current"]
        if rest[0] in _CONTEXT_SUBCOMMANDS:
            return ["context", *rest]
        if rest[0].startswith("-"):
            return ["context", "current", *rest]
        return ["context", "prepare", "--task", " ".join(rest)]
    if name in _COGNITION_ALIASES:
        return ["cognition", _COGNITION_ALIASES[name], *rest]
    if name == "remember":
        return ["knowledge", "remember", *rest]
    if name == "why":
        return ["knowledge", "why", *rest]
    if name == "conflicts":
        return ["knowledge", "conflicts", *rest]
    if name == "knowledge" and not rest:
        return ["knowledge", "status"]
    if name == "checkpoint":
        if rest and rest[0] in {"create", "inspect", "restore"}:
            return ["checkpoint", *rest]
        if rest and rest[0] == "promote":
            return ["cognition", "checkpoint", "--promote-required", *rest[1:]]
        return ["cognition", "checkpoint", *rest]
    if name == "verify" and not rest:
        return ["verify", "status"]
    if name == "finish":
        return ["work", "finish", *rest]
    if name == "complete":
        return ["work", "complete", *rest]
    return tokens


def _task_label(root: Path) -> str:
    path = root / ".agent-work" / "task.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "idle"
    if not isinstance(payload, dict):
        return "idle"
    task_id = str(payload.get("taskId") or "").strip()
    if task_id:
        return task_id[:12]
    return "work"


class AgentShell(cmd.Cmd):
    intro = None

    def __init__(self, root: Path, dispatcher: Dispatcher) -> None:
        super().__init__()
        self.root = root.resolve()
        self.dispatcher = dispatcher
        self.last_status = 0
        self._refresh_prompt()

    def _refresh_prompt(self) -> None:
        self.prompt = f"agent[{_task_label(self.root)}]> "

    def emptyline(self) -> bool:
        return False

    def execute_line(self, line: str) -> int:
        try:
            argv = normalize_command(line)
        except ShellCommandError as exc:
            print(f"agent shell: {exc}")
            self.last_status = 2
            return self.last_status
        if not argv:
            self.last_status = 0
            return 0
        try:
            status = self.dispatcher(argv)
        except SystemExit as exc:
            status = int(exc.code) if isinstance(exc.code, int) else 2
        self.last_status = int(status or 0)
        self._refresh_prompt()
        return self.last_status

    def default(self, line: str) -> bool:
        self.execute_line(line)
        return False

    def do_exit(self, arg: str) -> bool:
        """Exit the interactive shell."""
        return True

    def do_quit(self, arg: str) -> bool:
        """Exit the interactive shell."""
        return True

    def do_EOF(self, arg: str) -> bool:
        print()
        return True

    def do_help(self, arg: str) -> None:
        if arg:
            super().do_help(arg)
            return
        print("Agent DevTools shell — thin UX over the ordinary CLI; no shell-only project state.")
        print("Shortcuts:")
        print("  begin [goal]               -> begin / begin --goal <goal>")
        print("  status                     -> work status")
        print("  context                    -> context current")
        print("  context <task>             -> context prepare --task <task>")
        print("  decision|finding|assumption|requirement|question|evidence|observation <text>|--text <text> [--subject S]")
        print("  remember <event-id>        -> knowledge remember")
        print("  checkpoint                 -> classify required/advisory/session-only semantics; routine memory authority")
        print("  checkpoint promote         -> cognition checkpoint --promote-required")
        print("  cognition tool-failure ... -> bounded recovery after repeated external-tool failure")
        print("  why <knowledge-id>         -> knowledge why")
        print("  conflicts                  -> knowledge conflicts")
        print("  verify                     -> verify status")
        print("  verify research            -> guided research verification review")
        print("  verify research --confirm-all-pass -> explicit compact PASS attestation after review")
        print("  finish / complete          -> work finish / work complete")
        print("Any ordinary Agent DevTools command may also be entered unchanged.")


def configure_parser(
    parser: argparse.ArgumentParser,
    *,
    command_dest: str = "command",
) -> None:
    parser.add_argument(
        "--command",
        dest=command_dest,
        action="append",
        default=[],
        help="execute one shell line through the same dispatcher; repeatable; omit for interactive mode",
    )
    parser.add_argument("--no-banner", action="store_true", help="suppress the interactive startup banner")


def main(root: Path, args: argparse.Namespace, *, dispatcher: Dispatcher) -> int:
    shell = AgentShell(root, dispatcher)
    raw_commands = getattr(args, "shell_commands", None)
    if raw_commands is None:
        raw_commands = getattr(args, "command", [])
    if not isinstance(raw_commands, (list, tuple)):
        raw_commands = []
    commands = list(raw_commands)
    if commands:
        for line in commands:
            status = shell.execute_line(line)
            if status != 0:
                return status
        return 0
    intro = None if bool(getattr(args, "no_banner", False)) else (
        "Agent DevTools interactive shell. Type help for shortcuts; ordinary CLI commands also work."
    )
    try:
        shell.cmdloop(intro=intro)
    except KeyboardInterrupt:
        print()
        return 130
    return 0
