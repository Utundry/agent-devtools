from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from agent_devtools.cli import parser
from agent_devtools.project_overview import build_overview, main
from agent_devtools.work.journal import append_events
from agent_devtools.work.state import start_task, utc_now


class ProjectOverviewTests(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "VERSION").write_text("9.9.9\n", encoding="utf-8")
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": "research"}}),
            encoding="utf-8",
        )
        state = start_task(root, goal="Исследовать проект")
        append_events(
            root,
            task_id=state["taskId"],
            created_at_utc=utc_now(),
            events=[
                {"kind": "finding", "text": "Найден факт"},
                {"kind": "decision", "text": "Принято решение"},
                {"kind": "question", "text": "Открытый вопрос"},
            ],
        )
        return tmp, root

    def test_parser_exposes_project_overview(self):
        args = parser().parse_args(["project", "overview", "--json"])
        self.assertEqual("project", args.command)
        self.assertEqual("overview", args.project_command)
        self.assertTrue(args.json_output)

    def test_overview_aggregates_existing_state_without_inventing_efficiency(self):
        tmp, root = self.make_root()
        try:
            payload = build_overview(root)
            self.assertEqual("agent-devtools-project-overview", payload["format"])
            self.assertEqual(1, payload["formatVersion"])
            self.assertEqual("9.9.9", payload["projectVersion"])
            self.assertEqual("research", payload["profile"])
            self.assertEqual(1, payload["work"]["sessionsSeen"])
            self.assertEqual(3, payload["cognition"]["events"])
            self.assertEqual(1, payload["cognition"]["byKind"]["finding"])
            self.assertEqual(1, payload["cognition"]["byKind"]["decision"])
            self.assertEqual(1, payload["cognition"]["byKind"]["question"])
            self.assertEqual(0, payload["context"]["projections"])
            self.assertIsNone(payload["context"]["averageSelectedTokens"])
            self.assertNotIn("reduction", payload["context"])
            self.assertTrue(payload["readOnly"])
        finally:
            tmp.cleanup()

    def test_human_and_json_views_share_one_payload(self):
        tmp, root = self.make_root()
        try:
            human = io.StringIO()
            with redirect_stdout(human):
                self.assertEqual(
                    0,
                    main(root, SimpleNamespace(project_command="overview", json_output=False)),
                )
            self.assertIn("AGENT DEVTOOLS · PROJECT OVERVIEW", human.getvalue())
            self.assertIn("Semantic events:   3", human.getvalue())

            machine = io.StringIO()
            with redirect_stdout(machine):
                self.assertEqual(
                    0,
                    main(root, SimpleNamespace(project_command="overview", json_output=True)),
                )
            payload = json.loads(machine.getvalue())
            self.assertEqual(3, payload["cognition"]["events"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
