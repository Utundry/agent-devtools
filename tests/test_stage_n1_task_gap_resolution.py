from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_devtools.onboarding import managed_block
from agent_devtools.work.state import (
    TaskStateError,
    align_task,
    complete_task,
    load_task_state,
    start_task,
    task_alignment_ready,
)
from agent_devtools.workflow import capabilities, workflow_contract


class TaskGapResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": "general"}}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_new_work_starts_pending_and_finish_is_blocked(self) -> None:
        state = start_task(self.root, goal="Create a 3D Tetris game")
        self.assertEqual("pending", state["taskAlignment"]["status"])
        self.assertFalse(task_alignment_ready(state))
        with self.assertRaisesRegex(TaskStateError, "task-gap alignment"):
            complete_task(self.root)

    def test_material_gaps_require_proposal_then_explicit_user_approval(self) -> None:
        start_task(self.root, goal="Create a 3D Tetris game")
        state = align_task(
            self.root,
            material_gaps=["Target platform", "Interaction model"],
            proposal="Recommend browser delivery with keyboard controls",
        )
        self.assertEqual("clarification-required", state["taskAlignment"]["status"])
        self.assertFalse(state["taskAlignment"]["explicitUserApproval"])
        with self.assertRaisesRegex(TaskStateError, "task-gap alignment"):
            complete_task(self.root)

        state = align_task(
            self.root,
            user_approved=True,
            resolution="User approved browser delivery with keyboard controls",
        )
        self.assertTrue(task_alignment_ready(state))
        self.assertTrue(state["taskAlignment"]["explicitUserApproval"])
        self.assertEqual("completed", complete_task(self.root)["status"])

    def test_no_material_gaps_avoids_ceremonial_question(self) -> None:
        start_task(self.root, goal="Fix a concrete regression")
        state = align_task(self.root, no_material_gaps=True)
        self.assertTrue(task_alignment_ready(state))
        self.assertFalse(state["taskAlignment"]["explicitUserApproval"])
        self.assertIn("without user clarification", state["taskAlignment"]["resolution"])

    def test_no_material_gaps_accepts_optional_explicit_rationale(self) -> None:
        start_task(self.root, goal="Apply the exact supplied patch")
        state = align_task(
            self.root,
            no_material_gaps=True,
            resolution="Patch, target file, and acceptance criteria are explicit",
        )
        self.assertTrue(task_alignment_ready(state))
        self.assertFalse(state["taskAlignment"]["explicitUserApproval"])

    def test_goal_change_invalidates_previous_alignment(self) -> None:
        start_task(self.root, goal="Build a browser game")
        state = align_task(
            self.root,
            no_material_gaps=True,
            resolution="Browser target and acceptance criteria are explicit",
        )
        self.assertTrue(task_alignment_ready(state))
        from agent_devtools.work.state import update_task
        state = update_task(self.root, goal="Build a desktop game instead")
        self.assertEqual("pending", state["taskAlignment"]["status"])
        self.assertFalse(task_alignment_ready(state))

    def test_gap_recording_refuses_open_ended_question_without_proposal(self) -> None:
        start_task(self.root, goal="Build a game")
        with self.assertRaisesRegex(TaskStateError, "concrete proposal"):
            align_task(self.root, material_gaps=["Platform is unspecified"])

    def test_legacy_task_state_remains_resumable(self) -> None:
        start_task(self.root, goal="Legacy work")
        path = self.root / ".agent-work" / "task.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("taskAlignment")
        path.write_text(json.dumps(payload), encoding="utf-8")
        state = load_task_state(self.root)
        self.assertIsNotNone(state)
        self.assertTrue(task_alignment_ready(state))
        self.assertIn("legacy", state["taskAlignment"]["resolution"])

    def test_workflow_and_capabilities_expose_frictionless_alignment(self) -> None:
        contract = workflow_contract(self.root)
        phase_ids = [item["id"] for item in contract["phases"]]
        self.assertEqual(["orient", "start", "align", "work", "verify", "knowledge", "finish"], phase_ids)
        caps = capabilities(self.root)
        self.assertTrue(caps["commands"]["work"]["taskGapGate"])
        self.assertTrue(caps["commands"]["work"]["noGapFastPath"])
        self.assertFalse(caps["commands"]["knowledge"]["promotionRequired"])
        principles = " ".join(contract["principles"])
        self.assertIn("Autonomy starts after alignment", principles)
        self.assertIn("Never ask a question merely to satisfy the gate", principles)

    def test_agents_contract_requires_gap_resolution_without_questionnaire_ceremony(self) -> None:
        block = managed_block()
        self.assertIn("Task Gap Resolution Gate", block)
        self.assertIn("expensive rework", block)
        self.assertIn("concrete options", block)
        self.assertIn("without a user turn", block)
        self.assertIn("zero promotions", block)


if __name__ == "__main__":
    unittest.main()
