from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_devtools.cli import parser
from agent_devtools.onboarding import managed_block
from agent_devtools.work.cli import main_cognition
from agent_devtools.work.state import align_task, start_task, update_task
from agent_devtools.workflow import capabilities, workflow_contract


class FrictionlessSemanticCaptureTests(unittest.TestCase):
    def make_root(self, *, profile: str = "research"):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": profile}}),
            encoding="utf-8",
        )
        start_task(root, goal="Capture semantics", scope=["semantic-capture"])
        align_task(root, no_material_gaps=True, resolution="ready")
        return tmp, root

    def checkpoint_args(self, *, json_output: bool = False):
        return SimpleNamespace(
            cognition_command="checkpoint",
            promote_required=False,
            json_output=json_output,
        )

    def test_cognition_parser_accepts_natural_text_alias(self):
        args = parser().parse_args([
            "cognition", "requirement", "--text", "Research three architectures", "--subject", "research/nas",
        ])
        self.assertIsNone(args.text)
        self.assertEqual("Research three architectures", args.text_option)

    def test_cognition_text_alias_records_same_semantics(self):
        tmp, root = self.make_root()
        try:
            args = SimpleNamespace(
                cognition_command="finding",
                text=None,
                text_option="Reusable finding",
                subject="research/nas",
                json_output=True,
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_cognition(root, args)
            self.assertEqual(0, rc)
            payload = json.loads(out.getvalue())
            self.assertIn("Reusable finding", payload["findings"])
            self.assertEqual("research/nas", payload["subject"])
        finally:
            tmp.cleanup()

    def test_cognition_rejects_ambiguous_double_text(self):
        tmp, root = self.make_root()
        try:
            args = SimpleNamespace(
                cognition_command="finding",
                text="positional",
                text_option="optional",
                subject="research/nas",
                json_output=True,
            )
            err = io.StringIO()
            with contextlib.redirect_stdout(err):
                rc = main_cognition(root, args)
            self.assertEqual(2, rc)
            self.assertIn("either positionally or with --text", err.getvalue())
        finally:
            tmp.cleanup()

    def test_checkpoint_reports_session_only_and_says_no_promotion_needed(self):
        tmp, root = self.make_root()
        try:
            update_task(root, add_findings=["Local one-off finding"])
            update_task(root, add_assumptions=["Local assumption"])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_cognition(root, self.checkpoint_args())
            self.assertEqual(0, rc)
            text = out.getvalue()
            self.assertIn("session-only=2", text)
            self.assertIn("Durable knowledge: no promotion needed", text)
            self.assertIn("Do not call `agent knowledge promote`", text)
            self.assertIn("Next: agent work complete", text)
        finally:
            tmp.cleanup()

    def test_checkpoint_json_classifies_subjectless_events(self):
        tmp, root = self.make_root()
        try:
            update_task(root, add_findings=["Session finding"])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_cognition(root, self.checkpoint_args(json_output=True))
            self.assertEqual(0, rc)
            payload = json.loads(out.getvalue())
            self.assertEqual(1, len(payload["sessionOnlyEvents"]))
            self.assertEqual("finding", payload["sessionOnlyEvents"][0]["kind"])
            self.assertFalse(payload["guidance"]["promotionNeeded"])
        finally:
            tmp.cleanup()

    def test_subject_bearing_finding_is_advisory_not_required(self):
        tmp, root = self.make_root()
        try:
            update_task(
                root,
                add_findings=["Reusable finding"],
                semantic_subjects={"findings": "research/nas"},
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_cognition(root, self.checkpoint_args())
            self.assertEqual(0, rc)
            text = out.getvalue()
            self.assertIn("advisory=1", text)
            self.assertIn("Advisory candidates do not block completion", text)
            self.assertIn("knowledge remember", text)
        finally:
            tmp.cleanup()

    def test_onboarding_routes_research_to_guided_verify_and_checkpoint_memory(self):
        block = managed_block()
        self.assertIn("Do not call `knowledge promote` directly in routine work", block)
        self.assertIn("`python devtools/agent/agent.py verify research`", block)
        self.assertIn("checkpoint is the routine authority", block)

    def test_capabilities_and_workflow_advertise_frictionless_capture(self):
        tmp, root = self.make_root()
        try:
            caps = capabilities(root)
            self.assertTrue(caps["commands"]["cognition"]["textOptionAlias"])
            self.assertTrue(caps["commands"]["cognition"]["checkpointSessionOnlyClassification"])
            self.assertFalse(caps["commands"]["knowledge"]["routineDirectPromote"])
            self.assertEqual("verify research", caps["commands"]["verification"]["researchCanonicalCommand"])
            route = workflow_contract(root)["routineRoute"]
            self.assertIn("checkpoint", route["semanticCapture"])
            self.assertIn("expert", route["directKnowledgePromotion"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
