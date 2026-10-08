from __future__ import annotations
import contextlib, io, json, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace

from agent_devtools.cli import parser
from agent_devtools.onboarding import managed_block
from agent_devtools.work.cli import main_begin
from agent_devtools.work.state import align_task, start_task
from agent_devtools.workflow import capabilities, workflow_contract

class CanonicalSurfaceTests(unittest.TestCase):
    def make_root(self, profile="research"):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version":1,"work":{"profile":profile}}), encoding="utf-8"
        )
        start_task(root, goal="Research NAS backups", next_step="Record findings")
        align_task(root, no_material_gaps=True, resolution="ready")
        return tmp, root

    def begin_args(self, profile="research", json_output=True):
        return SimpleNamespace(
            goal=None, profile=profile, scope=[], constraint=[], done=[],
            next_action="", alignment_pending=False, replace=False,
            handoff=None, force=False, budget=1400, no_context=False,
            json_output=json_output,
        )

    def test_begin_accepts_matching_profile_hint(self):
        tmp, root = self.make_root()
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_begin(root, self.begin_args())
            self.assertEqual(0, rc)
            payload = json.loads(out.getvalue())
            self.assertEqual("research", payload["normalSurface"]["profile"])
            self.assertIn("agent verify research", payload["normalSurface"]["commands"])
        finally:
            tmp.cleanup()

    def test_begin_rejects_mismatched_profile_hint(self):
        tmp, root = self.make_root()
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = main_begin(root, self.begin_args(profile="analysis"))
            self.assertEqual(2, rc)
            self.assertIn("profile is already research", err.getvalue())
            self.assertIn("do not change it through begin", err.getvalue())
        finally:
            tmp.cleanup()

    def test_task_update_accepts_obvious_aliases(self):
        args = parser().parse_args([
            "task","update","--add-finding","f","--add-assumption","a",
            "--add-decision","d","--done","criterion",
        ])
        self.assertEqual(["f"], args.finding)
        self.assertEqual(["a"], args.assumption)
        self.assertEqual(["d"], args.decision)
        self.assertEqual(["criterion"], args.add_done)

    def test_begin_payload_exposes_normal_surface(self):
        tmp, root = self.make_root()
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main_begin(root, self.begin_args())
            surface = json.loads(out.getvalue())["normalSurface"]
            self.assertEqual(
                ["agent cognition", "agent verify research", "agent cognition checkpoint", "agent work complete"],
                surface["commands"],
            )
            self.assertIn("agent task update", surface["advancedPrimitives"])
            self.assertIn("agent work finish", surface["advancedPrimitives"])
            self.assertIn("agent knowledge promote", surface["advancedPrimitives"])
        finally:
            tmp.cleanup()

    def test_workflow_and_capabilities_separate_surfaces(self):
        tmp, root = self.make_root()
        try:
            route = workflow_contract(root)["routineRoute"]
            self.assertEqual("canonical-surface", route["surface"]["mode"])
            self.assertIn("task update", route["surface"]["advancedPrimitives"])
            caps = capabilities(root)["normalSurface"]
            self.assertEqual("research", caps["profile"])
            self.assertFalse(caps["advancedPrimitivesAreRoutine"])
        finally:
            tmp.cleanup()

    def test_onboarding_forbids_primitive_exploration(self):
        block = managed_block()
        self.assertIn("Normal surface", block)
        self.assertIn("Do not use `task update`", block)
        self.assertIn("Do not use `work finish`", block)
        self.assertIn("Do not explore advanced primitives", block)

if __name__ == "__main__":
    unittest.main()
