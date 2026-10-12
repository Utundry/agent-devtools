from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_devtools.cli import parser
from agent_devtools.onboarding import managed_block
from agent_devtools.work.cli import main_cognition, main_verify
from agent_devtools.work.completion import complete_work
from agent_devtools.work.state import (
    TaskStateError,
    align_task,
    load_task_state,
    start_task,
    update_task,
)
from agent_devtools.work.verification import run_verification_command
from agent_devtools.workflow import CLI_CONTRACT_VERSION, capabilities


class CliErgonomicsDogfoodTests(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": "research"}}),
            encoding="utf-8",
        )
        start_task(root, goal="Exercise CLI ergonomics")
        align_task(root, no_material_gaps=True, resolution="ready")
        return tmp, root

    def test_agents_prefer_robust_text_transport_for_shell_sensitive_content(self):
        block = managed_block()
        self.assertIn("for multiline text, Unicode, quotes, or other shell-sensitive content prefer UTF-8 `--stdin` or `--from-file`", block)
        self.assertIn("especially on Windows or through intermediate command layers", block)
        self.assertIn("When the agent already holds the text, prefer `--stdin`", block)
        self.assertIn("A materially different error or failed precondition is a distinct failure", block)

    def test_verify_status_parser_exposes_compact_json_controls(self):
        args = parser().parse_args(["verify", "status", "--summary", "--json"])
        self.assertTrue(args.summary)
        self.assertFalse(args.no_files)
        args = parser().parse_args(["verify", "status", "--no-files", "--json"])
        self.assertTrue(args.no_files)

    def test_verify_run_human_output_surfaces_success_stdout_preview(self):
        tmp, root = self.make_root()
        try:
            args = parser().parse_args([
                "verify", "run", "--label", "activation", "--",
                sys.executable, "-c", "print('Activated release 123')",
            ])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_verify(root, args)
            self.assertEqual(0, rc)
            text = out.getvalue()
            self.assertIn("evidence: executed", text)
            self.assertIn("preview: Activated release 123", text)
        finally:
            tmp.cleanup()

    def test_verify_status_summary_omits_file_arrays_but_keeps_source_counts(self):
        tmp, root = self.make_root()
        try:
            source = {
                "available": True,
                "gitHead": "abc",
                "changedFiles": ["src/a.py", "src/b.py"],
                "files": [
                    {"path": "src/a.py", "status": "modified", "sha256": "1" * 64},
                    {"path": "src/b.py", "status": "modified", "sha256": "2" * 64},
                ],
                "fingerprint": "same",
            }
            with patch(
                "agent_devtools.work.verification._verification_source_state",
                side_effect=[source, source],
            ), patch(
                "agent_devtools.work.verification._configured_command_relevance",
                return_value={"status": "relevant", "suite": "unit", "selectedSuites": ["unit"]},
            ):
                run_verification_command(
                    root,
                    command=[sys.executable, "-c", "print('ok')"],
                    label="unit",
                )
            args = parser().parse_args(["verify", "status", "--summary", "--json"])
            out = io.StringIO()
            with patch(
                "agent_devtools.work.verification._verification_source_state",
                return_value=source,
            ), contextlib.redirect_stdout(out):
                rc = main_verify(root, args)
            self.assertEqual(0, rc)
            payload = json.loads(out.getvalue())
            latest = payload["latest"]
            self.assertEqual(2, latest["sourceBinding"]["changedFileCount"])
            self.assertEqual(2, latest["currentSourceState"]["changedFileCount"])
            self.assertNotIn("files", json.dumps(latest))
            self.assertNotIn("changedFiles", json.dumps(latest))
        finally:
            tmp.cleanup()

    def test_verify_status_no_files_keeps_changed_paths_without_hash_rows(self):
        tmp, root = self.make_root()
        try:
            source = {
                "available": True,
                "gitHead": "abc",
                "changedFiles": ["src/a.py"],
                "files": [{"path": "src/a.py", "status": "modified", "sha256": "1" * 64}],
                "fingerprint": "same",
            }
            with patch(
                "agent_devtools.work.verification._verification_source_state",
                side_effect=[source, source],
            ):
                run_verification_command(root, command=[sys.executable, "-c", "print('ok')"])
            args = parser().parse_args(["verify", "status", "--no-files", "--json"])
            out = io.StringIO()
            with patch(
                "agent_devtools.work.verification._verification_source_state",
                return_value=source,
            ), contextlib.redirect_stdout(out):
                rc = main_verify(root, args)
            self.assertEqual(0, rc)
            latest = json.loads(out.getvalue())["latest"]
            self.assertEqual(["src/a.py"], latest["sourceBinding"]["state"]["changedFiles"])
            self.assertNotIn("files", latest["sourceBinding"]["state"])
            self.assertNotIn("files", latest["currentSourceState"])
        finally:
            tmp.cleanup()

    def test_resolve_blocker_is_fail_closed_and_supports_stdin(self):
        tmp, root = self.make_root()
        try:
            blocker = 'TLS CA bundle missing: "corp root"'
            update_task(root, add_blockers=[blocker])

            bad = parser().parse_args(["cognition", "resolve-blocker", "wrong-id"])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_cognition(root, bad)
            self.assertEqual(2, rc)
            self.assertIn("exact recorded blocker was not found", out.getvalue())
            self.assertEqual([blocker], load_task_state(root)["blockers"])

            good = parser().parse_args(["cognition", "resolve-blocker", "--stdin"])
            with patch("sys.stdin", io.StringIO(blocker + "\n")):
                rc = main_cognition(root, good)
            self.assertEqual(0, rc)
            self.assertEqual([], load_task_state(root)["blockers"])
        finally:
            tmp.cleanup()

    def test_work_complete_names_unresolved_blockers_and_safe_resolution_command(self):
        tmp, root = self.make_root()
        try:
            blocker = "Administrator phone is required"
            update_task(root, add_blockers=[blocker])
            with self.assertRaises(TaskStateError) as ctx:
                complete_work(root)
            message = str(ctx.exception)
            self.assertIn(blocker, message)
            self.assertIn("agent cognition resolve-blocker --stdin", message)
            self.assertIn("agent work complete", message)
        finally:
            tmp.cleanup()

    def test_capabilities_advertise_ergonomic_surface(self):
        tmp, root = self.make_root()
        try:
            caps = capabilities(root)
            self.assertGreaterEqual(CLI_CONTRACT_VERSION, 40)
            cognition = caps["commands"]["cognition"]
            verification = caps["commands"]["verification"]
            self.assertTrue(cognition["resolveBlockerFailClosed"])
            self.assertTrue(cognition["resolveBlockerStdin"])
            self.assertTrue(cognition["resolveBlockerFromFile"])
            self.assertTrue(verification["statusSummary"])
            self.assertTrue(verification["statusNoFiles"])
            self.assertTrue(verification["runSuccessPreview"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
