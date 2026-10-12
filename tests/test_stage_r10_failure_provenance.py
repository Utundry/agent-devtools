from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_devtools.cli import parser
from agent_devtools.work.cli import main_cognition
from agent_devtools.work.journal import events_for_task
from agent_devtools.work.state import align_task, load_task_state, start_task
from agent_devtools.work.verification import VerificationError, run_verification_command, verification_history
from agent_devtools.workflow import CLI_CONTRACT_VERSION, WORKFLOW_CONTRACT_VERSION, capabilities


class FailureProvenanceTests(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": "research"}}),
            encoding="utf-8",
        )
        state = start_task(root, goal="Recover from release infrastructure failure")
        align_task(root, no_material_gaps=True, resolution="ready")
        return tmp, root, state

    def failure_args(self, **overrides):
        values = dict(
            cognition_command="tool-failure",
            tool="verify",
            operation="release",
            error="spawn EPERM",
            importance="mandatory",
            kind="infrastructure",
            verification_id="",
            attempts=None,
            fallback="run outside sandbox",
            json_output=True,
        )
        values.update(overrides)
        return SimpleNamespace(**values)

    def call_failure(self, root, **overrides):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = main_cognition(root, self.failure_args(**overrides))
        return rc, json.loads(out.getvalue())

    def test_retry_count_is_journal_derived_and_legacy_attempts_cannot_force_ceiling(self):
        tmp, root, _ = self.make_root()
        try:
            rc1, first = self.call_failure(root, attempts=99)
            self.assertEqual(0, rc1)
            self.assertEqual(1, first["attempts"])
            self.assertEqual(99, first["legacyAttemptsIgnored"])
            self.assertTrue(first["retryAllowed"])
            rc2, second = self.call_failure(root)
            self.assertEqual(1, rc2)
            self.assertEqual(2, second["attempts"])
            self.assertFalse(second["retryAllowed"])
            self.assertEqual("semantic-journal", second["attemptsSource"])
        finally:
            tmp.cleanup()

    def test_different_error_is_a_distinct_failure_series(self):
        tmp, root, _ = self.make_root()
        try:
            _, first = self.call_failure(root, error="administrator phone missing")
            _, second = self.call_failure(root, error="TLS CA bundle missing")
            self.assertNotEqual(first["fingerprint"], second["fingerprint"])
            self.assertEqual(1, first["attempts"])
            self.assertEqual(1, second["attempts"])
            self.assertTrue(second["retryAllowed"])
        finally:
            tmp.cleanup()

    def test_failed_verification_can_be_linked_and_classified(self):
        tmp, root, state = self.make_root()
        try:
            failed = run_verification_command(
                root,
                command=[sys.executable, "-c", "import sys; print('spawn EPERM', file=sys.stderr); sys.exit(7)"],
                label="sandbox-build",
            )
            rc, failure = self.call_failure(root, verification_id="latest")
            self.assertEqual(0, rc)
            self.assertEqual(failed["id"], failure["verificationId"])
            self.assertEqual("infrastructure", failure["failureKind"])
            event = next(e for e in events_for_task(root, state["taskId"]) if e.get("id") == failure["eventId"])
            self.assertEqual(failed["id"], event["metadata"]["verificationId"])
            self.assertEqual("infrastructure", event["metadata"]["failureKind"])
        finally:
            tmp.cleanup()

    def test_invalid_verification_binding_is_a_normal_cli_error(self):
        tmp, root, _ = self.make_root()
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_cognition(root, self.failure_args(verification_id="missing-record"))
            self.assertEqual(2, rc)
            self.assertIn("verification record not found", out.getvalue())
            self.assertEqual([], load_task_state(root)["blockers"])
        finally:
            tmp.cleanup()

    def test_successful_fallback_preserves_failed_evidence_and_resolves_matching_blocker(self):
        tmp, root, _ = self.make_root()
        try:
            original = run_verification_command(
                root,
                command=[sys.executable, "-c", "import sys; sys.exit(7)"],
                label="sandbox-build",
            )
            self.call_failure(root, verification_id=original["id"])
            rc, failure = self.call_failure(root, verification_id=original["id"])
            self.assertEqual(1, rc)
            self.assertTrue(failure["blocking"])
            self.assertTrue(load_task_state(root)["blockers"])

            fallback = run_verification_command(
                root,
                command=[sys.executable, "-c", "print('Activated release')"],
                label="host-build",
                fallback_for=failure["fingerprint"],
            )
            self.assertEqual("pass", fallback["status"])
            self.assertEqual(failure["fingerprint"], fallback["fallbackFor"]["fingerprint"])
            self.assertEqual("infrastructure", fallback["fallbackFor"]["failureKind"])
            self.assertEqual("resolved", fallback["fallbackResolution"]["status"])
            self.assertEqual([], load_task_state(root)["blockers"])

            history = verification_history(root, limit=10)["records"]
            self.assertEqual("pass", history[0]["status"])
            self.assertTrue(any(item["id"] == original["id"] and item["status"] == "fail" for item in history))
        finally:
            tmp.cleanup()

    def test_failed_fallback_does_not_resolve_blocker(self):
        tmp, root, _ = self.make_root()
        try:
            self.call_failure(root)
            _, failure = self.call_failure(root)
            fallback = run_verification_command(
                root,
                command=[sys.executable, "-c", "import sys; sys.exit(9)"],
                fallback_for=failure["fingerprint"],
            )
            self.assertEqual("fail", fallback["status"])
            self.assertTrue(load_task_state(root)["blockers"])
        finally:
            tmp.cleanup()

    def test_successful_fallback_resets_equivalent_retry_series(self):
        tmp, root, _ = self.make_root()
        try:
            self.call_failure(root)
            _, failure = self.call_failure(root)
            run_verification_command(
                root,
                command=[sys.executable, "-c", "print('ok')"],
                fallback_for=failure["fingerprint"],
            )
            rc, again = self.call_failure(root)
            self.assertEqual(0, rc)
            self.assertEqual(1, again["attempts"])
            self.assertTrue(again["retryAllowed"])
        finally:
            tmp.cleanup()

    def test_unknown_fallback_fingerprint_fails_before_execution(self):
        tmp, root, _ = self.make_root()
        try:
            with patch("agent_devtools.work.verification.subprocess.run") as run:
                with self.assertRaisesRegex(VerificationError, "does not match a recorded tool failure"):
                    run_verification_command(
                        root,
                        command=[sys.executable, "-c", "print('must not execute')"],
                        fallback_for="deadbeefdeadbeef",
                    )
                run.assert_not_called()
        finally:
            tmp.cleanup()

    def test_cli_exposes_new_failure_and_fallback_contract(self):
        failure = parser().parse_args([
            "cognition", "tool-failure",
            "--tool", "verify",
            "--error", "spawn EPERM",
            "--importance", "mandatory",
            "--kind", "infrastructure",
            "--verification-id", "latest",
        ])
        self.assertEqual("infrastructure", failure.kind)
        self.assertEqual("latest", failure.verification_id)
        fallback = parser().parse_args([
            "verify", "run", "--fallback-for", "0123456789abcdef", "--",
            sys.executable, "-c", "print('ok')",
        ])
        self.assertEqual("0123456789abcdef", fallback.fallback_for)

    def test_capabilities_advertise_provenance_contract(self):
        tmp, root, _ = self.make_root()
        try:
            caps = capabilities(root)
            self.assertGreaterEqual(CLI_CONTRACT_VERSION, 41)
            self.assertGreaterEqual(WORKFLOW_CONTRACT_VERSION, 24)
            cognition = caps["commands"]["cognition"]
            verification = caps["commands"]["verification"]
            self.assertEqual("semantic-journal", cognition["toolFailureRetryCountSource"])
            self.assertTrue(cognition["toolFailureVerificationBinding"])
            self.assertTrue(verification["fallbackForFailure"])
            self.assertTrue(verification["fallbackResolvesMatchingBlocker"])
            self.assertTrue(verification["failureProvenancePreserved"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
