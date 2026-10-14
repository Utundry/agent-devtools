import argparse
import json
import tempfile
import unittest
from pathlib import Path

from agent_devtools.work.state import align_task, load_task_state, start_task, TaskStateError
from agent_devtools.work.cli import configure_work_parser


class AlignmentExpectationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        start_task(self.root, goal="Add document preview")

    def test_optional_ui_alignment_is_persisted(self):
        state = align_task(
            self.root, no_material_gaps=True,
            interpretation="Preview from document details, preserve print",
            ui_scenario="Open document then preview",
            ui_entry_point="Document details",
            ui_interaction="One click to preview; back without loss",
            ui_risks="Avoid extra modal and duplicate entry",
        )
        self.assertEqual(state["taskAlignment"]["status"], "ready")
        self.assertEqual(load_task_state(self.root)["taskAlignment"]["uiAlignment"]["entryPoint"], "Document details")

    def test_clarification_preserves_notes_and_requires_approval(self):
        align_task(self.root, no_material_gaps=True, interpretation="First interpretation")
        state = align_task(self.root, material_gaps=["One act or many?"], proposal="One act for several positions", ui_scenario="Select positions")
        self.assertEqual(state["taskAlignment"]["status"], "clarification-required")
        with self.assertRaises(TaskStateError):
            align_task(self.root, no_material_gaps=True)
        state = align_task(self.root, user_approved=True, resolution="User approved combined act")
        self.assertEqual(state["taskAlignment"]["uiAlignment"]["scenario"], "Select positions")
        self.assertTrue(state["taskAlignment"]["explicitUserApproval"])

    def test_plain_task_without_ui_remains_valid(self):
        state = align_task(self.root, no_material_gaps=True)
        self.assertEqual(state["taskAlignment"]["status"], "ready")
        self.assertEqual(state["taskAlignment"]["uiAlignment"]["scenario"], "")

    def test_cli_flags_are_optional(self):
        parser = argparse.ArgumentParser()
        configure_work_parser(parser)
        args = parser.parse_args(["align", "--no-material-gaps", "--ui-entry-point", "Document details"])
        self.assertEqual(args.ui_entry_point, "Document details")
        self.assertEqual(parser.parse_args(["align", "--no-material-gaps"]).ui_scenario, "")


if __name__ == "__main__":
    unittest.main()
