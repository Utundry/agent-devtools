from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_devtools.onboarding import managed_block
from agent_devtools.work.cli import main_verify
from agent_devtools.work.state import TaskStateError, align_task, start_task
from agent_devtools.workflow import CLI_CONTRACT_VERSION, WORKFLOW_CONTRACT_VERSION, capabilities


class AgentDisciplineVerificationClarityTests(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": "research"}}),
            encoding="utf-8",
        )
        start_task(root, goal="Prepare hardware recommendation")
        align_task(root, no_material_gaps=True, resolution="ready")
        return tmp, root

    def args(self, **overrides):
        values = dict(
            verify_command="research",
            arithmetic="pass",
            sourcing="pass",
            assumptions="warn",
            knowledge="pass",
            unresolved_questions="pass",
            confirm_all_pass=False,
            evidence=["benchmarks reviewed"],
            summary="Prepared recommendation; hardware run still pending",
            json_output=True,
        )
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_research_warn_is_explicit_but_completion_gate_remains_pass(self):
        tmp, root = self.make_root()
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_verify(root, self.args())
            self.assertEqual(0, rc)
            payload = json.loads(out.getvalue())
            record = payload["record"]
            self.assertEqual("pass", record["status"])
            self.assertEqual("warn", record["assessmentStatus"])
            self.assertTrue(record["completionEligible"])
            self.assertEqual(["assumptions"], record["warningChecks"])
        finally:
            tmp.cleanup()

    def test_human_research_warn_never_prints_bare_pass(self):
        tmp, root = self.make_root()
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_verify(root, self.args(json_output=False))
            self.assertEqual(0, rc)
            text = out.getvalue()
            self.assertIn("verification: WARN · research-bundle · gate=PASS", text)
            self.assertIn("warning checks: assumptions", text)
            self.assertIn("does not prove unperformed execution/real-world validation", text)
            self.assertNotIn("verification: PASS · research-bundle", text)
        finally:
            tmp.cleanup()

    def test_user_approval_without_recorded_gap_has_actionable_non_ritual_hint(self):
        tmp, root = self.make_root()
        try:
            with self.assertRaises(TaskStateError) as ctx:
                align_task(root, user_approved=True, resolution="approved")
            message = str(ctx.exception)
            self.assertIn("previously recorded material task gaps", message)
            self.assertIn("begin already reported alignment ready", message)
            self.assertIn("do not call work align", message)
        finally:
            tmp.cleanup()

    def test_managed_onboarding_encodes_begin_once_and_batch_preference(self):
        block = managed_block()
        self.assertIn("Do not rerun `begin` for ordinary user follow-ups", block)
        self.assertIn("prefer `cognition batch`", block)
        self.assertIn("`work align --user-approved` is valid only after", block)

    def test_capabilities_advertise_discipline_and_warning_semantics(self):
        tmp, root = self.make_root()
        try:
            caps = capabilities(root)
            self.assertGreaterEqual(CLI_CONTRACT_VERSION, 35)
            self.assertGreaterEqual(WORKFLOW_CONTRACT_VERSION, 23)
            self.assertFalse(caps["commands"]["begin"]["ordinaryFollowupReentryRequired"])
            self.assertTrue(caps["commands"]["begin"]["interruptionRecoveryOnly"])
            self.assertTrue(caps["commands"]["cognition"]["batchPreferredForMultiple"])
            self.assertTrue(caps["commands"]["work"]["userApprovalRequiresRecordedGap"])
            verification = caps["commands"]["verification"]
            self.assertTrue(verification["researchAssessmentStatus"])
            self.assertTrue(verification["researchWarningChecksExplicit"])
            self.assertTrue(verification["completionGateSeparated"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
