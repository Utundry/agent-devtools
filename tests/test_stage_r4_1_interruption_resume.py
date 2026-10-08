from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_devtools.onboarding import managed_block
from agent_devtools.work.cli import main_begin
from agent_devtools.work.state import align_task, load_task_state, start_task, update_task
from agent_devtools.workflow import capabilities, workflow_contract


class InterruptionResumeTests(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": "research"}}), encoding="utf-8"
        )
        state = start_task(
            root,
            goal="Research NAS backup architecture",
            scope=["research/nas"],
            next_step="Verify research sources",
        )
        align_task(root, no_material_gaps=True, resolution="ready")
        update_task(root, add_findings=["Already completed finding"])
        return tmp, root, state["taskId"]

    def args(self, json_output=True):
        return SimpleNamespace(
            goal=None, scope=[], constraint=[], done=[], next_action="",
            alignment_pending=False, replace=False, handoff=None, force=False,
            budget=1400, no_context=False, json_output=json_output,
        )

    def test_begin_without_goal_resumes_same_active_task(self):
        tmp, root, task_id = self.make_root()
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_begin(root, self.args())
            self.assertEqual(0, rc)
            payload = json.loads(out.getvalue())
            self.assertEqual("resumed", payload["entry"]["action"])
            self.assertEqual(task_id, payload["entry"]["task"]["taskId"])
            self.assertEqual("Verify research sources", payload["nextAction"])
            self.assertEqual("active-task-resume", payload["recovery"]["mode"])
            self.assertTrue(payload["recovery"]["interruptionSafe"])
            self.assertFalse(payload["recovery"]["conversationMemoryAuthoritative"])
        finally:
            tmp.cleanup()

    def test_human_output_marks_project_state_authoritative(self):
        tmp, root, _ = self.make_root()
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_begin(root, self.args(False))
            self.assertEqual(0, rc)
            text = out.getvalue()
            self.assertIn("interruption recovery", text)
            self.assertIn("project task state is authoritative", text)
            self.assertIn("do not reconstruct progress from chat memory", text)
        finally:
            tmp.cleanup()

    def test_existing_progress_survives_resume(self):
        tmp, root, _ = self.make_root()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                main_begin(root, self.args())
            self.assertIn("Already completed finding", load_task_state(root)["findings"])
        finally:
            tmp.cleanup()

    def test_workflow_advertises_interruption_resume(self):
        tmp, root, _ = self.make_root()
        try:
            route = workflow_contract(root)["routineRoute"]
            self.assertIn("begin without --goal", route["interruptionResume"])
            caps = capabilities(root)["commands"]["begin"]
            self.assertTrue(caps["interruptionResume"])
            self.assertTrue(caps["resumeWithoutGoal"])
            self.assertFalse(caps["conversationMemoryAuthoritative"])
        finally:
            tmp.cleanup()

    def test_onboarding_requires_begin_after_interruption(self):
        block = managed_block()
        self.assertIn("transport/chat/SSE interruption", block)
        self.assertIn("run `begin` without `--goal`", block)
        self.assertIn("Do not reconstruct progress from conversation memory", block)


if __name__ == "__main__":
    unittest.main()
