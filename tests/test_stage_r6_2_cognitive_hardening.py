from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_devtools.work.cli import main_verify
from agent_devtools.work.context_projection import prepare_context
from agent_devtools.work.knowledge import KNOWLEDGE_FORMAT, KNOWLEDGE_VERSION
from agent_devtools.work.sources import add_source
from agent_devtools.work.state import align_task, start_task, update_task
from agent_devtools.workflow import CLI_CONTRACT_VERSION, WORKFLOW_CONTRACT_VERSION, capabilities


class CognitiveHardeningTests(unittest.TestCase):
    def make_root(self, *, goal="Choose minimal LMM browser testing workstation", scope=None):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": "research"}}),
            encoding="utf-8",
        )
        state = start_task(root, goal=goal, scope=list(scope or ["hardware/lmm/frontend-testing"]))
        align_task(root, no_material_gaps=True, resolution="ready")
        return tmp, root, state

    def add_decision(self, root, record_id, *, statement, subject, scope=None, task_goal="", task_id="old-task"):
        record = {
            "format": KNOWLEDGE_FORMAT,
            "formatVersion": KNOWLEDGE_VERSION,
            "id": record_id,
            "kind": "decision",
            "subject": subject,
            "status": "active",
            "lifecycleStatus": "active",
            "statement": statement,
            "scope": list(scope or []),
            "confidence": None,
            "evidenceRefs": [],
            "anchors": [],
            "supersedes": [],
            "sourceRefs": [],
            "sourceSession": {
                "taskId": task_id,
                "taskGoal": task_goal,
                "eventId": "event-" + record_id,
            },
            "changedFiles": [],
            "createdAtUtc": "2026-10-08T00:00:00Z",
            "updatedAtUtc": "2026-10-08T00:00:00Z",
        }
        path = root / ".agent-knowledge" / "decisions" / f"{record_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def research_args(self, **overrides):
        values = dict(
            verify_command="research",
            arithmetic=None,
            sourcing=None,
            assumptions=None,
            knowledge=None,
            unresolved_questions=None,
            confirm_all_pass=False,
            evidence=[],
            summary="",
            json_output=True,
        )
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_cross_task_lexical_only_match_needs_source_goal_support(self):
        tmp, root, _ = self.make_root()
        try:
            self.add_decision(
                root,
                "backup-power",
                subject="primary-recommendation",
                statement="Recommend minimal cost architecture with practical tradeoffs",
                task_goal="Compare home backup power architectures and autonomy",
            )
            payload = prepare_context(root)
            self.assertNotIn("backup-power", [x["id"] for x in payload["knowledge"]])
            self.assertGreaterEqual(payload["omitted"]["irrelevant"], 1)
        finally:
            tmp.cleanup()

    def test_related_cross_task_lexical_match_remains_available(self):
        tmp, root, _ = self.make_root()
        try:
            self.add_decision(
                root,
                "lmm-related",
                subject="hardware/lmm",
                statement="Use 32 GB RAM for local LMM browser testing",
                task_goal="Research LMM frontend browser testing hardware",
            )
            payload = prepare_context(root)
            self.assertIn("lmm-related", [x["id"] for x in payload["knowledge"]])
        finally:
            tmp.cleanup()

    def test_explicit_scope_match_is_not_blocked_by_cross_task_isolation(self):
        tmp, root, _ = self.make_root()
        try:
            self.add_decision(
                root,
                "scope-related",
                subject="hardware/lmm",
                statement="Preserve this scoped hardware rule",
                scope=["hardware/lmm"],
                task_goal="An unrelated historical task",
            )
            payload = prepare_context(root)
            self.assertIn("scope-related", [x["id"] for x in payload["knowledge"]])
        finally:
            tmp.cleanup()

    def test_research_review_warns_when_current_task_has_no_source_or_evidence(self):
        tmp, root, _ = self.make_root()
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_verify(root, self.research_args())
            self.assertEqual(1, rc)
            payload = json.loads(out.getvalue())
            warnings = {x["id"]: x for x in payload["contextWarnings"]}
            self.assertIn("missing-source-provenance", warnings)
            self.assertEqual(0, warnings["missing-source-provenance"]["sourceCount"])
            self.assertEqual(0, warnings["missing-source-provenance"]["evidenceCount"])
        finally:
            tmp.cleanup()

    def test_current_task_source_removes_missing_source_warning(self):
        tmp, root, state = self.make_root()
        try:
            source = add_source(root, url="https://example.com/spec", title="Example spec")
            self.assertEqual(state["taskId"], source["sourceSession"]["taskId"])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_verify(root, self.research_args())
            self.assertEqual(1, rc)
            payload = json.loads(out.getvalue())
            ids = {x["id"] for x in payload["contextWarnings"]}
            self.assertNotIn("missing-source-provenance", ids)
        finally:
            tmp.cleanup()

    def test_subject_bearing_open_question_is_flagged_as_potentially_material(self):
        tmp, root, _ = self.make_root()
        try:
            update_task(
                root,
                add_open_questions=["Which model family changes the GPU requirement?"],
                semantic_subjects={"openQuestions": "models"},
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_verify(root, self.research_args())
            self.assertEqual(1, rc)
            payload = json.loads(out.getvalue())
            warning = next(x for x in payload["contextWarnings"] if x["id"] == "potentially-material-open-questions")
            self.assertEqual(1, warning["count"])
            self.assertEqual(["models"], warning["subjects"])
        finally:
            tmp.cleanup()

    def test_subjectless_open_question_keeps_general_warning_only(self):
        tmp, root, _ = self.make_root()
        try:
            update_task(root, add_open_questions=["What is the exact SSD street price?"])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_verify(root, self.research_args())
            self.assertEqual(1, rc)
            payload = json.loads(out.getvalue())
            ids = {x["id"] for x in payload["contextWarnings"]}
            self.assertIn("open-questions", ids)
            self.assertNotIn("potentially-material-open-questions", ids)
        finally:
            tmp.cleanup()

    def test_capabilities_advertise_cognitive_hardening_without_new_workflow(self):
        tmp, root, _ = self.make_root()
        try:
            caps = capabilities(root)
            self.assertGreaterEqual(CLI_CONTRACT_VERSION, 29)
            self.assertGreaterEqual(WORKFLOW_CONTRACT_VERSION, 21)
            self.assertTrue(caps["commands"]["context"]["crossTaskLexicalIsolation"])
            self.assertTrue(caps["commands"]["verification"]["researchSourceAwareness"])
            self.assertTrue(caps["commands"]["verification"]["researchMaterialQuestionAwareness"])
            self.assertTrue(caps["commands"]["source"]["taskSessionProvenance"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
