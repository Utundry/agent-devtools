from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from agent_devtools.shell import AgentShell, configure_parser, main, normalize_command


class InteractiveShellTests(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        return tmp, root

    def test_shortcuts_normalize_to_existing_cli_commands(self):
        self.assertEqual(["work", "status"], normalize_command("status"))
        self.assertEqual(["context", "current"], normalize_command("context"))
        self.assertEqual(["knowledge", "remember", "E-12"], normalize_command("remember E-12"))
        self.assertEqual(["cognition", "checkpoint"], normalize_command("checkpoint"))
        self.assertEqual(["knowledge", "why", "K-7"], normalize_command("why K-7"))
        self.assertEqual(["knowledge", "conflicts"], normalize_command("conflicts"))
        self.assertEqual(["verify", "status"], normalize_command("verify"))
        self.assertEqual(["work", "finish"], normalize_command("finish"))

    def test_semantic_shortcut_preserves_one_quoted_text_argument_and_options(self):
        self.assertEqual(
            ["cognition", "decision", "Keep JSON canonical", "--subject", "knowledge/storage"],
            normalize_command('decision "Keep JSON canonical" --subject knowledge/storage'),
        )
        self.assertEqual(
            ["cognition", "open-question", "Need migration?"],
            normalize_command('question "Need migration?"'),
        )

    def test_context_free_text_maps_to_prepare_but_explicit_subcommands_pass_through(self):
        self.assertEqual(
            ["context", "prepare", "--task", "document requisites"],
            normalize_command("context document requisites"),
        )
        self.assertEqual(
            ["context", "prepare", "--stage", "verification"],
            normalize_command("context prepare --stage verification"),
        )

    def test_unknown_command_is_passed_through_unchanged(self):
        self.assertEqual(["work", "complete", "--no-cache"], normalize_command("work complete --no-cache"))
        self.assertEqual(["knowledge", "status", "--json"], normalize_command("knowledge status --json"))

    def test_shell_dispatches_exact_normalized_argv_and_creates_no_state_itself(self):
        tmp, root = self.make_root()
        calls = []
        try:
            shell = AgentShell(root, lambda argv: calls.append(list(argv or [])) or 0)
            before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            self.assertEqual(0, shell.execute_line('finding "Reusable edge" --subject parser/edge'))
            after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            self.assertEqual(before, after)
            self.assertEqual(
                [["cognition", "finding", "Reusable edge", "--subject", "parser/edge"]],
                calls,
            )
        finally:
            tmp.cleanup()

    def test_batch_mode_stops_on_first_existing_cli_failure(self):
        tmp, root = self.make_root()
        calls = []
        try:
            def dispatch(argv):
                calls.append(list(argv or []))
                return 1 if argv == ["verify", "status"] else 0
            args = argparse.Namespace(command=["status", "verify", "finish"], no_banner=True)
            self.assertEqual(1, main(root, args, dispatcher=dispatch))
            self.assertEqual([["work", "status"], ["verify", "status"]], calls)
        finally:
            tmp.cleanup()

    def test_prompt_is_read_only_projection_of_existing_task_state(self):
        tmp, root = self.make_root()
        try:
            task = root / ".agent-work" / "task.json"
            task.parent.mkdir(parents=True)
            task.write_text(json.dumps({"taskId": "W-1234567890abcdef"}), encoding="utf-8")
            shell = AgentShell(root, lambda argv: 0)
            self.assertEqual("agent[W-1234567890]> ", shell.prompt)
            self.assertEqual({"taskId": "W-1234567890abcdef"}, json.loads(task.read_text(encoding="utf-8")))
        finally:
            tmp.cleanup()

    def test_parser_exposes_repeatable_noninteractive_command_mode(self):
        parser = argparse.ArgumentParser()
        configure_parser(parser)
        args = parser.parse_args(["--command", "status", "--command", "finish", "--no-banner"])
        self.assertEqual(["status", "finish"], args.command)
        self.assertTrue(args.no_banner)


if __name__ == "__main__":
    unittest.main()
