from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_devtools.work.cli import main_cognition
from agent_devtools.work.context_projection import prepare_context
from agent_devtools.work.knowledge import KNOWLEDGE_FORMAT, KNOWLEDGE_VERSION
from agent_devtools.work.state import align_task, start_task
from agent_devtools.workflow import CLI_CONTRACT_VERSION, WORKFLOW_CONTRACT_VERSION, capabilities


class ContextRecallCliFrictionTests(unittest.TestCase):
    def make_root(self, *, goal="Compare best cost hardware for a local 70B LLM"):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": "research"}}),
            encoding="utf-8",
        )
        state = start_task(root, goal=goal)
        align_task(root, no_material_gaps=True, resolution="ready")
        return tmp, root, state

    def add_record(
        self,
        root,
        record_id,
        *,
        subject,
        statement,
        source_goal,
        source_task="old-task",
        kind="decision",
    ):
        status = "open" if kind in {"finding", "open_question"} else "active"
        record = {
            "format": KNOWLEDGE_FORMAT,
            "formatVersion": KNOWLEDGE_VERSION,
            "id": record_id,
            "kind": kind,
            "subject": subject,
            "status": status,
            "lifecycleStatus": "active",
            "statement": statement,
            "scope": [],
            "confidence": "high",
            "evidenceRefs": [],
            "anchors": [],
            "supersedes": [],
            "sourceRefs": [],
            "sourceSession": {
                "taskId": source_task,
                "taskGoal": source_goal,
                "eventId": "event-" + record_id,
            },
            "changedFiles": [],
            "createdAtUtc": "2026-10-09T00:00:00Z",
            "updatedAtUtc": "2026-10-09T00:00:00Z",
        }
        path = root / ".agent-knowledge" / f"{kind}s" / f"{record_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def cognition_args(self, command, text, subject=""):
        return SimpleNamespace(
            cognition_command=command,
            text=text,
            text_option=None,
            stdin_input=False,
            from_file=None,
            subject=subject or None,
            json_output=False,
        )

    def test_related_cross_task_record_is_cued_without_entering_primary_budget(self):
        tmp, root, _ = self.make_root()
        try:
            self.add_record(
                root,
                "related-hardware",
                subject="recommended-mvp",
                statement="Prefer a multi-GPU workstation topology.",
                source_goal="Choose minimal cost hardware for local LLM browser testing",
            )
            payload = prepare_context(root, budget=256)
            self.assertNotIn("related-hardware", [x["id"] for x in payload["knowledge"]])
            related = {x["id"]: x for x in payload["possiblyRelated"]}
            self.assertIn("related-hardware", related)
            self.assertEqual("related-cue", related["related-hardware"]["mode"])
            self.assertLessEqual(payload["possiblyRelatedRecords"], 3)
            self.assertEqual(0, payload["estimatedTokens"])
            self.assertGreater(payload["relatedEstimatedTokens"], 0)
        finally:
            tmp.cleanup()

    def test_unrelated_cross_task_record_remains_irrelevant(self):
        tmp, root, _ = self.make_root()
        try:
            self.add_record(
                root,
                "backup-power",
                subject="primary-recommendation",
                statement="Use a hybrid inverter and battery.",
                source_goal="Compare home backup power architecture and solar autonomy",
            )
            payload = prepare_context(root)
            self.assertNotIn("backup-power", [x["id"] for x in payload["knowledge"]])
            self.assertNotIn("backup-power", [x["id"] for x in payload["possiblyRelated"]])
            self.assertGreaterEqual(payload["omitted"]["irrelevant"], 1)
        finally:
            tmp.cleanup()

    def test_related_channel_is_bounded_to_three_and_separate_from_primary_budget(self):
        tmp, root, _ = self.make_root()
        try:
            for index in range(5):
                self.add_record(
                    root,
                    f"related-{index}",
                    subject=f"hardware-note-{index}",
                    statement=f"Prior workstation topology note {index}.",
                    source_goal=f"Research low cost local LLM hardware option {index}",
                )
            payload = prepare_context(root, budget=128)
            self.assertEqual(3, payload["possiblyRelatedRecords"])
            self.assertEqual(0, payload["estimatedTokens"])
            self.assertLessEqual(payload["relatedEstimatedTokens"], 240)
        finally:
            tmp.cleanup()

    def test_cognition_human_output_is_compact_not_full_history(self):
        tmp, root, _ = self.make_root()
        try:
            out1 = io.StringIO()
            with contextlib.redirect_stdout(out1):
                rc = main_cognition(
                    root,
                    self.cognition_args("finding", "First detailed finding", "finding/one"),
                )
            self.assertEqual(0, rc)

            out2 = io.StringIO()
            with contextlib.redirect_stdout(out2):
                rc = main_cognition(
                    root,
                    self.cognition_args("finding", "Second detailed finding", "finding/two"),
                )
            self.assertEqual(0, rc)
            text = out2.getvalue()
            self.assertEqual(["recorded: finding · finding/two"], text.splitlines())
            self.assertNotIn("Second detailed finding", text)
            self.assertNotIn("First detailed finding", text)
            self.assertNotIn("findings=2", text)
        finally:
            tmp.cleanup()

    def test_capabilities_expose_related_cues_and_exit_code_semantics(self):
        tmp, root, _ = self.make_root()
        try:
            caps = capabilities(root)
            self.assertGreaterEqual(CLI_CONTRACT_VERSION, 31)
            self.assertGreaterEqual(WORKFLOW_CONTRACT_VERSION, 21)
            self.assertTrue(caps["commands"]["context"]["possiblyRelatedCues"])
            self.assertTrue(caps["commands"]["cognition"]["compactHumanOutput"])
            semantics = caps["exitCodeSemantics"]
            self.assertIn("actionable-gate", semantics["1"])
            self.assertIn("invalid", semantics["2"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
