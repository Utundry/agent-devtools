from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_devtools.cli import _command_inventory, main, parser
from agent_devtools.work.cli import main_verify
from agent_devtools.work.state import align_task, start_task, update_task
from agent_devtools.workflow import CLI_CONTRACT_VERSION, WORKFLOW_CONTRACT_VERSION, capabilities


class AgentUxHardeningTests(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": "research"}}),
            encoding="utf-8",
        )
        start_task(root, goal="Research resilient power")
        align_task(root, no_material_gaps=True, resolution="ready")
        update_task(
            root,
            add_assumptions=["Average load is estimated"],
            add_open_questions=["Winter PV yield is not yet measured"],
        )
        return tmp, root

    def research_args(self, **overrides):
        values = dict(
            verify_command="research",
            arithmetic=None,
            sourcing=None,
            assumptions=None,
            knowledge=None,
            unresolved_questions=None,
            confirm_all_pass=False,
            evidence=[],
            summary="",
            json_output=False,
        )
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_research_review_surfaces_current_uncertainty_without_blocking(self):
        tmp, root = self.make_root()
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_verify(root, self.research_args())
            self.assertEqual(1, rc)
            text = out.getvalue()
            self.assertIn("Context warning:", text)
            self.assertIn("1 explicit assumption", text)
            self.assertIn("1 open question", text)
            self.assertIn("explicitly retained and are non-blocking", text)
        finally:
            tmp.cleanup()

    def test_confirm_all_pass_records_pass_but_keeps_context_warnings_in_json(self):
        tmp, root = self.make_root()
        try:
            out = io.StringIO()
            args = self.research_args(
                confirm_all_pass=True,
                evidence=["Internal measurement reviewed"],
                summary="Reviewed retained uncertainty",
                json_output=True,
            )
            with contextlib.redirect_stdout(out):
                rc = main_verify(root, args)
            self.assertEqual(0, rc)
            payload = json.loads(out.getvalue())
            self.assertEqual("pass", payload["record"]["status"])
            warning_ids = {x["id"] for x in payload["contextWarnings"]}
            self.assertTrue({"active-assumptions", "open-questions"}.issubset(warning_ids))
        finally:
            tmp.cleanup()

    def test_cognition_add_guess_gets_actionable_hint_but_is_not_a_command(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = main(["cognition", "add", "--stdin"])
        self.assertEqual(2, rc)
        message = err.getvalue()
        self.assertIn("`add` is not a semantic type", message)
        self.assertIn("cognition observation --stdin", message)
        self.assertNotIn("cognition add", _command_inventory(parser()))

    def test_capabilities_advertise_hardening_without_changing_workflow_version(self):
        tmp, root = self.make_root()
        try:
            caps = capabilities(root)
            self.assertGreaterEqual(CLI_CONTRACT_VERSION, 28)
            self.assertEqual(21, WORKFLOW_CONTRACT_VERSION)
            self.assertTrue(caps["commands"]["cognition"]["naturalGuessHints"])
            self.assertTrue(caps["commands"]["verification"]["researchContextWarnings"])
            self.assertNotIn("agent cognition add", caps["normalSurface"]["commands"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
