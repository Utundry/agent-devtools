from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_devtools.cli import parser
from agent_devtools.shell import normalize_command
from agent_devtools.work.cli import main_begin, main_cognition
from agent_devtools.work.knowledge import load_records, promote
from agent_devtools.work.state import align_task, complete_task, start_task, update_task


class WorkRitualTests(unittest.TestCase):
    def make_root(self, *, profile: str = "general"):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": profile}}),
            encoding="utf-8",
        )
        return tmp, root

    def begin_args(self, **overrides):
        values = {
            "goal": "Improve ritual",
            "scope": ["ritual"],
            "constraint": [],
            "done": [],
            "next_action": "Implement",
            "alignment_pending": False,
            "replace": False,
            "handoff": None,
            "force": False,
            "budget": 1400,
            "no_context": False,
            "json_output": True,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def checkpoint_args(self, *, promote_required: bool):
        return SimpleNamespace(
            cognition_command="checkpoint",
            promote_required=promote_required,
            json_output=True,
        )

    def test_parser_and_shell_expose_normal_ritual(self):
        args = parser().parse_args(["begin", "--goal", "Ship ritual", "--json"])
        self.assertEqual("begin", args.command)
        self.assertEqual("Ship ritual", args.goal)
        self.assertEqual(["begin", "--goal", "Ship ritual"], normalize_command("begin Ship ritual"))
        self.assertEqual(
            ["cognition", "checkpoint", "--promote-required"],
            normalize_command("checkpoint promote"),
        )
        self.assertEqual(["cognition", "checkpoint"], normalize_command("checkpoint"))

    def test_begin_starts_and_projects_existing_durable_context(self):
        tmp, root = self.make_root()
        try:
            start_task(root, goal="old", scope=["ritual"])
            align_task(root, no_material_gaps=True, resolution="ok")
            update_task(
                root,
                add_decisions=["Keep ritual thin"],
                semantic_subjects={"decisions": "ritual/architecture"},
            )
            promote(
                root,
                kind="decision",
                text="Keep ritual thin",
                subject="ritual/architecture",
                scope=["ritual"],
            )
            complete_task(root)

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_begin(root, self.begin_args())
            self.assertEqual(0, rc)
            payload = json.loads(out.getvalue())
            self.assertEqual(["begin", "work", "checkpoint", "complete"], payload["ritual"])
            self.assertEqual("started", payload["entry"]["action"])
            self.assertGreaterEqual(payload["contextProjection"]["selectedRecords"], 1)
            self.assertEqual("planning", payload["contextProjection"]["stage"])
            self.assertTrue(payload["knowledge"]["ok"])
        finally:
            tmp.cleanup()

    def test_begin_resumes_active_task_without_recreating_state(self):
        tmp, root = self.make_root()
        try:
            start_task(root, goal="active", scope=["ritual"], next_step="continue")
            align_task(root, no_material_gaps=True, resolution="ok")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_begin(root, self.begin_args(goal=None, scope=[], next_action=""))
            self.assertEqual(0, rc)
            payload = json.loads(out.getvalue())
            self.assertEqual("resumed", payload["entry"]["action"])
            self.assertEqual("active", payload["entry"]["task"]["goal"])
            self.assertEqual("continue", payload["nextAction"])
        finally:
            tmp.cleanup()

    def test_checkpoint_is_read_only_until_explicit_required_promotion(self):
        tmp, root = self.make_root()
        try:
            start_task(root, goal="ritual", scope=["ritual"])
            align_task(root, no_material_gaps=True, resolution="ok")
            update_task(
                root,
                add_decisions=["Use explicit promotion"],
                semantic_subjects={"decisions": "ritual/promotion"},
            )

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_cognition(root, self.checkpoint_args(promote_required=False))
            self.assertEqual(1, rc)
            self.assertEqual([], load_records(root))
            before = json.loads(out.getvalue())
            self.assertEqual(1, len(before["requiredPromotions"]))

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_cognition(root, self.checkpoint_args(promote_required=True))
            self.assertEqual(0, rc)
            after = json.loads(out.getvalue())
            self.assertTrue(after["clean"])
            self.assertEqual(1, len(after["promotion"]["promoted"]))
            self.assertEqual(1, len(load_records(root)))
        finally:
            tmp.cleanup()

    def test_fresh_clone_recovers_git_durable_memory_for_begin(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            first = base / "first"
            second = base / "second"
            first.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(first)], check=True)
            subprocess.run(["git", "-C", str(first), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(first), "config", "user.email", "test@example.invalid"], check=True)
            (first / "agent-tools.json").write_text(
                json.dumps({"version": 1, "work": {"profile": "general"}}),
                encoding="utf-8",
            )
            start_task(first, goal="old", scope=["portable"])
            align_task(first, no_material_gaps=True, resolution="ok")
            update_task(
                first,
                add_requirements=["Memory follows Git"],
                semantic_subjects={"requirements": "portable/memory"},
            )
            promote(
                first,
                kind="requirement",
                text="Memory follows Git",
                subject="portable/memory",
                scope=["portable"],
            )
            subprocess.run(["git", "-C", str(first), "add", "agent-tools.json", ".agent-knowledge"], check=True)
            subprocess.run(["git", "-C", str(first), "commit", "-q", "-m", "memory"], check=True)
            subprocess.run(["git", "clone", "-q", str(first), str(second)], check=True)

            self.assertFalse((second / ".agent-work" / "task.json").exists())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_begin(second, self.begin_args(goal="Continue portable work", scope=["portable"]))
            self.assertEqual(0, rc)
            payload = json.loads(out.getvalue())
            subjects = [item["subject"] for item in payload["contextProjection"]["knowledge"]]
            self.assertIn("portable/memory", subjects)
            self.assertEqual("planning", payload["contextProjection"]["stage"])


if __name__ == "__main__":
    unittest.main()
