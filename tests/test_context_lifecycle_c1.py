from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_devtools.work.completion import complete_work
from agent_devtools.work.journal import (
    SemanticJournalError,
    append_event,
    events_for_task,
    journal_path,
)
from agent_devtools.work.knowledge import promote
from agent_devtools.work.semantic_closeout import semantic_checkpoint
from agent_devtools.work.state import (
    TaskStateError,
    align_task,
    load_task_state,
    start_task,
    update_task,
    utc_now,
)
from agent_devtools.work.verification import record_verification


class StructuredSemanticJournalTests(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": "general"}}),
            encoding="utf-8",
        )
        return tmp, root

    def ready(self, root: Path):
        state = start_task(root, goal="Exercise semantic journal")
        align_task(root, no_material_gaps=True)
        return state

    def test_task_cognition_is_mirrored_into_append_only_sqlite_journal(self) -> None:
        tmp, root = self.make_root()
        try:
            state = self.ready(root)
            update_task(
                root,
                add_findings=["The renderer bypasses the shared requisites policy"],
                semantic_subjects={"findings": "documents/requisites"},
            )
            update_task(
                root,
                add_findings=["The renderer bypasses the shared requisites policy"],
                semantic_subjects={"findings": "documents/requisites"},
            )
            events = events_for_task(root, state["taskId"])
            self.assertTrue(journal_path(root).is_file())
            self.assertEqual(1, len(events))
            self.assertEqual("finding", events[0]["kind"])
            self.assertEqual("documents/requisites", events[0]["subject"])
        finally:
            tmp.cleanup()

    def test_task_projection_rolls_back_when_journal_write_fails(self) -> None:
        tmp, root = self.make_root()
        try:
            self.ready(root)
            with patch(
                "agent_devtools.work.journal.append_events",
                side_effect=SemanticJournalError("forced journal failure"),
            ):
                with self.assertRaisesRegex(TaskStateError, "task state rolled back"):
                    update_task(
                        root,
                        add_decisions=["Must not survive failed journal write"],
                    )

            self.assertNotIn(
                "Must not survive failed journal write",
                load_task_state(root)["decisions"],
            )
        finally:
            tmp.cleanup()

    def test_subject_bearing_decision_requires_durable_representation(self) -> None:
        tmp, root = self.make_root()
        try:
            self.ready(root)
            text = "Document rendering remains client-heavy"
            update_task(
                root,
                add_decisions=[text],
                semantic_subjects={"decisions": "documents/rendering"},
            )

            report = semantic_checkpoint(root)

            self.assertFalse(report["clean"])
            self.assertEqual(
                "documents/rendering",
                report["requiredPromotions"][0]["subject"],
            )

            promote(
                root,
                kind="decision",
                text=text,
                subject="documents/rendering",
            )

            report = semantic_checkpoint(root)

            self.assertTrue(report["clean"])
            self.assertEqual([], report["requiredPromotions"])
        finally:
            tmp.cleanup()

    def test_subjectless_local_decision_does_not_create_bureaucracy(self) -> None:
        tmp, root = self.make_root()
        try:
            self.ready(root)

            update_task(
                root,
                add_decisions=["Use a temporary local variable for this repair"],
            )

            report = semantic_checkpoint(root)

            self.assertTrue(report["clean"])
            self.assertEqual([], report["requiredPromotions"])
        finally:
            tmp.cleanup()

    def test_findings_are_review_candidates_not_closeout_blockers(self) -> None:
        tmp, root = self.make_root()
        try:
            self.ready(root)

            update_task(
                root,
                add_findings=["A reusable edge case was found"],
                semantic_subjects={"findings": "parser/edge-case"},
            )

            report = semantic_checkpoint(root)

            self.assertTrue(report["clean"])
            self.assertEqual(1, len(report["advisoryCandidates"]))
        finally:
            tmp.cleanup()

    def test_verification_is_recorded_as_semantic_event(self) -> None:
        tmp, root = self.make_root()
        try:
            state = self.ready(root)

            record_verification(
                root,
                label="review",
                status="pass",
                evidence=["manual review"],
            )

            events = events_for_task(root, state["taskId"])
            verification = [
                item
                for item in events
                if item["kind"] == "verification"
            ]

            self.assertEqual(1, len(verification))
            self.assertEqual("review: pass", verification[0]["text"])
        finally:
            tmp.cleanup()

    def test_completion_blocks_unrepresented_material_decision(self) -> None:
        tmp, root = self.make_root()
        try:
            self.ready(root)
            text = "Final artifacts are immutable"

            update_task(
                root,
                add_decisions=[text],
                semantic_subjects={"decisions": "artifact/finalization"},
            )

            with self.assertRaisesRegex(TaskStateError, "semantic closeout"):
                complete_work(root)

            promote(
                root,
                kind="decision",
                text=text,
                subject="artifact/finalization",
            )

            record_verification(
                root,
                label="review",
                status="pass",
                evidence=["reviewed"],
            )

            payload = complete_work(root)

            self.assertTrue(payload["semanticCloseout"]["clean"])
            self.assertEqual("completed", payload["task"]["status"])
        finally:
            tmp.cleanup()

    def test_observation_is_supported_without_polluting_task_projection(self) -> None:
        tmp, root = self.make_root()
        try:
            state = self.ready(root)

            append_event(
                root,
                task_id=state["taskId"],
                kind="observation",
                text="Local exploratory note",
                created_at_utc=utc_now(),
            )

            report = semantic_checkpoint(root)

            self.assertEqual(1, report["byKind"]["observation"])
            self.assertTrue(report["clean"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()