from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_devtools.work.cli import main_work
from agent_devtools.work.completion import complete_work
from agent_devtools.work.state import TaskStateError, align_task, load_task_state, start_task


class CompletionReviewFacadeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "agent-tools.json").write_text('{"version":1,"work":{"profile":"research"}}')
        start_task(self.root, goal="Research review")
        align_task(self.root, no_material_gaps=True)

    def args(self, **kw):
        values = dict(work_command="complete", knowledge_review=None, acceptance_review=None,
                      review_note="", summary=None, no_cache=False, resume=False,
                      verify=False, verify_argv=[], verify_label="completion-command",
                      verify_timeout=None, verify_output_limit=1600, json_output=True)
        values.update(kw)
        return SimpleNamespace(**values)

    def test_api_keeps_actionable_research_error(self):
        with self.assertRaisesRegex(TaskStateError, "agent verify research"):
            complete_work(self.root)

    def test_cli_missing_review_does_not_close_task(self):
        self.assertEqual(2, main_work(self.root, self.args()))
        self.assertEqual("active", load_task_state(self.root)["status"])

    def test_cli_supplied_review_keeps_machine_gate(self):
        self.assertEqual(2, main_work(self.root, self.args(
            knowledge_review="unchanged", acceptance_review="pending",
            review_note="User acceptance delegated; result not yet tested")))
        self.assertEqual("active", load_task_state(self.root)["status"])

    def test_cli_review_is_persisted_only_on_success(self):
        from agent_devtools.work.verification import record_research_bundle
        record_research_bundle(self.root, checks={
            "arithmetic":"pass", "sourcing":"pass", "assumptions":"pass",
            "knowledge":"pass", "unresolved_questions":"pass"}, summary="Reviewed")
        self.assertEqual(0, main_work(self.root, self.args(
            knowledge_review="unchanged", acceptance_review="not-applicable",
            review_note="Pure internal research with verification recorded")))
        self.assertEqual("completed", load_task_state(self.root)["status"])
        self.assertEqual("not-applicable", load_task_state(self.root)["completionReview"]["acceptanceReview"])


if __name__ == "__main__":
    unittest.main()
