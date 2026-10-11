from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_devtools.work.verification import (
    assess_verification_record,
    run_verification_command,
    verification_status,
)


class VerificationBindingTests(unittest.TestCase):
    def test_executed_evidence_binds_before_after_source_state_and_relevance(self) -> None:
        before = {
            "available": True,
            "gitHead": "abc",
            "changedFiles": ["src/a.py"],
            "files": [{"path": "src/a.py", "status": "modified", "sha256": "1" * 64}],
            "fingerprint": "before",
        }
        after = {**before, "fingerprint": "after"}
        relevance = {"status": "relevant", "suite": "unit", "selectedSuites": ["unit"]}
        with tempfile.TemporaryDirectory() as td, \
             patch("agent_devtools.work.verification._verification_source_state", side_effect=[before, after]), \
             patch("agent_devtools.work.verification._configured_command_relevance", return_value=relevance):
            record = run_verification_command(
                Path(td),
                command=[sys.executable, "-c", "print('ok')"],
            )
        self.assertEqual("before", record["sourceBinding"]["beforeFingerprint"])
        self.assertEqual("after", record["sourceBinding"]["afterFingerprint"])
        self.assertTrue(record["sourceBinding"]["changedDuringExecution"])
        self.assertEqual("relevant", record["relevance"]["status"])

    def test_status_marks_pass_stale_when_current_source_fingerprint_changes(self) -> None:
        source = {
            "available": True,
            "gitHead": "abc",
            "changedFiles": ["src/a.py"],
            "files": [],
            "fingerprint": "verified",
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch("agent_devtools.work.verification._verification_source_state", return_value=source), \
                 patch("agent_devtools.work.verification._configured_command_relevance", return_value={"status": "unknown"}):
                run_verification_command(root, command=[sys.executable, "-c", "print('ok')"])
            current = {**source, "fingerprint": "changed"}
            with patch("agent_devtools.work.verification._verification_source_state", return_value=current):
                latest = verification_status(root)["latest"]
        self.assertEqual("pass", latest["status"])
        self.assertEqual("stale", latest["sourceFreshness"])

    def test_assessment_keeps_unknown_for_legacy_or_attested_record(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            item = assess_verification_record(
                Path(td),
                {"status": "pass", "verificationKind": "attestation"},
            )
        self.assertEqual("unknown", item["sourceFreshness"])

    def test_unrecognized_arbitrary_command_relevance_stays_unknown(self) -> None:
        source = {
            "available": True,
            "gitHead": "abc",
            "changedFiles": ["src/a.py"],
            "files": [],
            "fingerprint": "same",
        }
        with tempfile.TemporaryDirectory() as td, \
             patch("agent_devtools.work.verification._verification_source_state", side_effect=[source, source]):
            record = run_verification_command(
                Path(td),
                command=[sys.executable, "-c", "print('custom')"],
            )
        self.assertEqual("unknown", record["relevance"]["status"])


if __name__ == "__main__":
    unittest.main()
