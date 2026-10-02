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

    def test_failed_dev_verification_does_not_finish(self) -> None:
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
