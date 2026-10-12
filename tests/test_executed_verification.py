from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from agent_devtools.cli import parser
from agent_devtools.work.verification import (
    _output_preview,
    record_verification,
    run_verification_command,
    verification_history,
    verification_status,
)


class ExecutedVerificationTests(unittest.TestCase):
    def test_run_records_pass_with_machine_captured_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            record = run_verification_command(
                root,
                command=[
                    sys.executable,
                    "-c",
                    "import sys; print('привет'); print(sys.argv[1])",
                    "value with spaces",
                ],
                label="unicode-command",
            )
            self.assertEqual("pass", record["status"])
            self.assertEqual("executed-command", record["verificationKind"])
            self.assertEqual(0, record["exitCode"])
            self.assertFalse(record["timedOut"])
            self.assertIn("привет", record["stdoutPreview"])
            self.assertIn("value with spaces", record["stdoutPreview"])
            self.assertEqual(64, len(record["stdoutSha256"]))
            self.assertEqual(3, len(record["evidence"]))
            self.assertIn("command-exit:0", record["evidence"])
            self.assertTrue(any(item.startswith("stdout-sha256:") for item in record["evidence"]))
            self.assertEqual(record["id"], verification_status(root)["latest"]["id"])

    def test_output_preview_preserves_head_and_tail(self) -> None:
        preview = _output_preview(("HEAD-" + ("x" * 100) + "-TAIL").encode("utf-8"), 20)
        self.assertTrue(preview.startswith("HEAD-"))
        self.assertTrue(preview.endswith("-TAIL"))
        self.assertIn("<truncated", preview)

    def test_output_preview_head_tail_remains_unicode_safe(self) -> None:
        preview = _output_preview(("начало-" + ("я" * 80) + "-конец").encode("utf-8"), 18)
        self.assertTrue(preview.startswith("начало-"))
        self.assertTrue(preview.endswith("-конец"))
        self.assertNotIn("\ufffd", preview)

    def test_run_records_failure_instead_of_claiming_pass(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            record = run_verification_command(
                root,
                command=[sys.executable, "-c", "import sys; print('bad', file=sys.stderr); sys.exit(7)"],
                label="failing-command",
            )
            self.assertEqual("fail", record["status"])
            self.assertEqual(7, record["exitCode"])
            self.assertIn("bad", record["stderrPreview"])

    def test_manual_record_remains_explicit_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            record = record_verification(
                Path(td),
                label="manual-review",
                status="pass",
                evidence=["reviewed manually"],
            )
            self.assertEqual("attestation", record["verificationKind"])
            self.assertNotIn("exitCode", record)

    def test_verify_run_parser_keeps_command_argv_without_shell_quoting(self) -> None:
        args = parser().parse_args([
            "verify", "run", "--label", "unit", "--",
            sys.executable, "-c", "print('ok')", "argument with spaces",
        ])
        self.assertEqual("run", args.verify_command)
        self.assertEqual("unit", args.label)
        self.assertTrue(args.argv)
        self.assertIn("argument with spaces", args.argv)

    def test_history_is_bounded_and_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = record_verification(root, label="first", status="pass")
            second = run_verification_command(root, command=[sys.executable, "-c", "print('second')"], label="second")
            history = verification_history(root, limit=2)
            self.assertEqual(2, history["count"])
            self.assertEqual([second["id"], first["id"]], [item["id"] for item in history["records"]])

    def test_verify_run_supports_utf8_summary_from_file(self) -> None:
        args = parser().parse_args([
            "verify", "run", "--summary-from-file", "summary.txt", "--",
            sys.executable, "-c", "print('ok')",
        ])
        self.assertEqual(Path("summary.txt"), args.summary_from_file)
        self.assertEqual("", args.summary)

    def test_status_and_history_accept_preview_controls(self) -> None:
        status = parser().parse_args(["verify", "status", "--preview"])
        history = parser().parse_args(["verify", "history", "--limit", "3", "--preview"])
        self.assertTrue(status.preview)
        self.assertEqual(3, history.limit)
        self.assertTrue(history.preview)


if __name__ == "__main__":
    unittest.main()
