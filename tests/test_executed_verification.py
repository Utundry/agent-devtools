from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from agent_devtools.cli import parser
from agent_devtools.work.verification import (
    record_verification,
    run_verification_command,
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
            self.assertEqual(record["id"], verification_status(root)["latest"]["id"])

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


if __name__ == "__main__":
    unittest.main()
