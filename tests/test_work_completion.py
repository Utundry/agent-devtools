from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_devtools.work.completion import complete_work
from agent_devtools.work.state import TaskStateError, align_task, load_task_state, start_task
from agent_devtools.work.verification import record_verification

class WorkCompletionTests(unittest.TestCase):
    def make_root(self, profile: str):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": profile}}),
            encoding="utf-8",
        )
        return tmp, root

    def ready(self, root: Path) -> None:
        start_task(root, goal="Complete one focused task")
        align_task(root, no_material_gaps=True)

    def test_non_dev_uses_fresh_recorded_verification(self) -> None:
        tmp, root = self.make_root("general")
        try:
            self.ready(root)
            record_verification(root, label="review", status="pass", evidence=["reviewed"])
            payload = complete_work(root)
            self.assertEqual("completed", payload["task"]["status"])
            self.assertFalse(payload["verificationPerformed"])
        finally:
            tmp.cleanup()

    def test_non_dev_never_fabricates_verification(self) -> None:
        tmp, root = self.make_root("general")
        try:
            self.ready(root)
            with self.assertRaisesRegex(TaskStateError, "verification record"):
                complete_work(root)
            self.assertEqual("active", load_task_state(root)["status"])
        finally:
            tmp.cleanup()

    def test_dev_runs_affected_check_then_finishes(self) -> None:
        tmp, root = self.make_root("development")
        try:
            self.ready(root)
            latest = {
                "status": "pass",
                "completedAtUtc": "9999-12-31T23:59:59+00:00",
                "runDirectory": ".agent-work/runs/test",
            }
            with patch(
                "agent_devtools.work.completion._run_development_verification",
                return_value=(0, "PASS"),
            ) as run_check, patch(
                "agent_devtools.work.completion.validated_check_report",
                return_value=latest,
            ):
                payload = complete_work(root, no_cache=True, resume=True)
            run_check.assert_called_once_with(root, no_cache=True, resume=True)
            self.assertTrue(payload["verificationPerformed"])
            self.assertEqual("completed", payload["task"]["status"])
        finally:
            tmp.cleanup()

    def test_adaptive_completion_promotes_required_semantics_after_executed_pass(self) -> None:
        tmp, root = self.make_root("general")
        try:
            self.ready(root)
            required = {
                "requiredPromotions": [
                    {
                        "eventId": "evt-1",
                        "kind": "decision",
                        "subject": "architecture",
                        "text": "keep direct argv",
                    }
                ]
            }
            clean = {
                "requiredPromotions": [],
                "advisoryCandidates": [],
                "sessionOnlyEvents": [],
                "clean": True,
            }
            executed = {
                "status": "pass",
                "exitCode": 0,
                "completedAtUtc": "9999-12-31T23:59:59+00:00",
            }
            with patch(
                "agent_devtools.work.completion.run_verification_command",
                return_value=executed,
            ), patch(
                "agent_devtools.work.completion.latest_verification",
                return_value=None,
            ), patch(
                "agent_devtools.work.completion.semantic_checkpoint",
                side_effect=[required, clean],
            ), patch(
                "agent_devtools.work.completion.remember_knowledge",
                return_value={"id": "knowledge-1"},
            ) as remember:
                payload = complete_work(
                    root,
                    verification_command=["python", "-m", "unittest"],
                    verification_label="unit",
                )
            remember.assert_called_once_with(root, event_id="evt-1")
            self.assertEqual(["knowledge-1"], payload["semanticPromotions"])
            self.assertEqual("executed-command", payload["verificationAction"])
            self.assertEqual("completed", payload["task"]["status"])
        finally:
            tmp.cleanup()

    def test_failed_adaptive_command_does_not_promote_or_finish(self) -> None:
        tmp, root = self.make_root("general")
        try:
            self.ready(root)
            with patch(
                "agent_devtools.work.completion.run_verification_command",
                return_value={
                    "status": "fail",
                    "exitCode": 7,
                    "completedAtUtc": "9999-12-31T23:59:59+00:00",
                },
            ), patch(
                "agent_devtools.work.completion.remember_knowledge",
            ) as remember:
                with self.assertRaisesRegex(TaskStateError, "completion verification command failed"):
                    complete_work(root, verification_command=["python", "bad.py"])
            remember.assert_not_called()
            self.assertEqual("active", load_task_state(root)["status"])
        finally:
            tmp.cleanup()

    def test_clean_tree_with_newer_head_blocks_obviously_stale_task(self) -> None:
        tmp, root = self.make_root("development")
        try:
            self.ready(root)
            with patch(
                "agent_devtools.work.completion.git_changed_files",
                return_value=set(),
            ), patch(
                "agent_devtools.work.completion._git_head_commit_epoch",
                return_value=200,
            ), patch(
                "agent_devtools.work.completion._task_activity_epoch",
                return_value=100,
            ), patch(
                "agent_devtools.work.completion._git_head_reflog_subject",
                return_value="pull: Fast-forward",
            ), patch(
                "agent_devtools.work.completion._run_development_verification",
            ) as run_check:
                with self.assertRaisesRegex(TaskStateError, "active task may be stale"):
                    complete_work(root)
            run_check.assert_not_called()
            self.assertEqual("active", load_task_state(root)["status"])
        finally:
            tmp.cleanup()

    def test_newer_head_from_local_commit_does_not_trigger_stale_guard(self) -> None:
        tmp, root = self.make_root("development")
        try:
            self.ready(root)
            latest = {
                "status": "pass",
                "completedAtUtc": "9999-12-31T23:59:59+00:00",
                "runDirectory": ".agent-work/runs/test",
            }
            with patch(
                "agent_devtools.work.completion.git_changed_files",
                return_value=set(),
            ), patch(
                "agent_devtools.work.completion._git_head_commit_epoch",
                return_value=200,
            ), patch(
                "agent_devtools.work.completion._task_activity_epoch",
                return_value=100,
            ), patch(
                "agent_devtools.work.completion._git_head_reflog_subject",
                return_value="commit: current task",
            ), patch(
                "agent_devtools.work.completion._run_development_verification",
                return_value=(0, "PASS"),
            ), patch(
                "agent_devtools.work.completion.validated_check_report",
                return_value=latest,
            ):
                payload = complete_work(root, no_cache=True)
            self.assertEqual("completed", payload["task"]["status"])
        finally:
            tmp.cleanup()

    def test_canonical_changes_prevent_stale_task_guessing(self) -> None:
        tmp, root = self.make_root("development")
        try:
            self.ready(root)
            latest = {
                "status": "pass",
                "completedAtUtc": "9999-12-31T23:59:59+00:00",
                "runDirectory": ".agent-work/runs/test",
            }
            with patch(
                "agent_devtools.work.completion.git_changed_files",
                return_value={"agent_devtools/work/completion.py"},
            ), patch(
                "agent_devtools.work.completion._git_head_commit_epoch",
            ) as head_time, patch(
                "agent_devtools.work.completion._run_development_verification",
                return_value=(0, "PASS"),
            ), patch(
                "agent_devtools.work.completion.validated_check_report",
                return_value=latest,
            ):
                payload = complete_work(root, no_cache=True)
            head_time.assert_not_called()
            self.assertEqual("completed", payload["task"]["status"])
        finally:
            tmp.cleanup()

    def test_failed_dev_verification_does_not_finish(self) -> None:
        # Existing verification-failure behavior remains unchanged.
        tmp, root = self.make_root("development")
        try:
            self.ready(root)
            with patch(
                "agent_devtools.work.completion._run_development_verification",
                return_value=(1, "FAIL"),
            ):
                with self.assertRaisesRegex(TaskStateError, "verification failed"):
                    complete_work(root)
            self.assertEqual("active", load_task_state(root)["status"])
        finally:
            tmp.cleanup()

    def test_knowledge_conflict_blocks_before_verification(self) -> None:
        tmp, root = self.make_root("development")
        try:
            self.ready(root)
            with patch(
                "agent_devtools.work.completion.validate_knowledge",
                return_value={"ok": False, "records": 2, "conflicts": [{"subject": "x"}], "danglingSupersedes": []},
            ), patch(
                "agent_devtools.work.completion._run_development_verification"
            ) as run_check:
                with self.assertRaisesRegex(TaskStateError, "durable knowledge"):
                    complete_work(root)
            run_check.assert_not_called()
        finally:
            tmp.cleanup()

if __name__ == "__main__":
    unittest.main()
