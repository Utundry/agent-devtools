from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_devtools.work.entry import WorkEntryError, enter_work
from agent_devtools.work.state import (
    align_task,
    complete_task,
    load_task_state,
    start_task,
    task_alignment_ready,
)


class WorkEntryTests(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": "general"}}),
            encoding="utf-8",
        )
        return tmp, root

    def test_clean_workspace_requires_goal(self) -> None:
        tmp, root = self.make_root()
        try:
            with self.assertRaisesRegex(WorkEntryError, "provide --goal"):
                enter_work(root, include_context=False)
        finally:
            tmp.cleanup()

    def test_new_task_can_start_and_take_no_gap_fast_path_in_one_action(self) -> None:
        tmp, root = self.make_root()
        try:
            payload = enter_work(
                root,
                goal="Implement focused change",
                next_action="Inspect target module",
                no_material_gaps=True,
                include_context=False,
            )
            self.assertEqual("started", payload["action"])
            self.assertTrue(payload["alignmentReady"])
            self.assertTrue(task_alignment_ready(load_task_state(root)))
        finally:
            tmp.cleanup()

    def test_existing_active_task_is_resumed_without_mutation(self) -> None:
        tmp, root = self.make_root()
        try:
            original = start_task(root, goal="Continue existing work", next_step="Next")
            payload = enter_work(root, include_context=False)
            self.assertEqual("resumed", payload["action"])
            self.assertEqual(original["taskId"], payload["task"]["taskId"])
            self.assertFalse(payload["alignmentReady"])
        finally:
            tmp.cleanup()

    def test_active_different_goal_fails_closed_without_replace(self) -> None:
        tmp, root = self.make_root()
        try:
            start_task(root, goal="Existing")
            with self.assertRaisesRegex(WorkEntryError, "active task already exists"):
                enter_work(root, goal="Different", include_context=False)
            self.assertEqual("Existing", load_task_state(root)["goal"])
        finally:
            tmp.cleanup()

    def test_completed_task_allows_clean_new_start_with_goal(self) -> None:
        tmp, root = self.make_root()
        try:
            start_task(root, goal="Old")
            align_task(root, no_material_gaps=True)
            complete_task(root)
            old_id = load_task_state(root)["taskId"]
            payload = enter_work(
                root,
                goal="New",
                no_material_gaps=True,
                include_context=False,
            )
            self.assertEqual("started", payload["action"])
            self.assertEqual("New", payload["task"]["goal"])
            self.assertNotEqual(old_id, payload["task"]["taskId"])
        finally:
            tmp.cleanup()

    def test_existing_material_gaps_cannot_be_erased_by_fast_path(self) -> None:
        tmp, root = self.make_root()
        try:
            start_task(root, goal="Ambiguous work")
            align_task(
                root,
                material_gaps=["Deployment target"],
                proposal="Use staging first",
            )
            with self.assertRaisesRegex(WorkEntryError, "previously identified material gaps"):
                enter_work(root, no_material_gaps=True, include_context=False)
        finally:
            tmp.cleanup()

    def test_handoff_entry_delegates_restore_and_returns_restored_task(self) -> None:
        tmp, root = self.make_root()
        try:
            bundle = root / "handoff.zip"

            def fake_resume(_root, _path, *, target, force, budget, include_context):
                start_task(root, goal="Restored")
                return {
                    "kind": "workspace-snapshot",
                    "brief": {"format": "agent-devtools-work-brief", "workingStateFingerprint": "after"},
                    "originalWorkingStateFingerprint": "before",
                    "currentWorkingStateFingerprint": "after",
                }

            with patch(
                "agent_devtools.work.entry.resume_handoff",
                side_effect=fake_resume,
            ) as resume:
                payload = enter_work(root, handoff=bundle, include_context=False)
            self.assertEqual("handoff-resumed", payload["action"])
            self.assertEqual("Restored", payload["task"]["goal"])
            self.assertTrue(resume.called)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
