from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_devtools.onboarding import managed_block
from agent_devtools.work.cli import main_begin, main_cognition, main_verify
from agent_devtools.work.completion import complete_work
from agent_devtools.work.state import TaskStateError, align_task, start_task, update_task
from agent_devtools.work.verification import latest_verification


class GuidedWorkLifecycleTests(unittest.TestCase):
    def make_root(self, *, profile: str = "research"):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": profile}}),
            encoding="utf-8",
        )
        return tmp, root

    def checkpoint_args(self, *, promote_required: bool = False, json_output: bool = False):
        return SimpleNamespace(
            cognition_command="checkpoint",
            promote_required=promote_required,
            json_output=json_output,
        )

    def research_args(self, *, confirm_all_pass: bool = False, **checks):
        values = {
            "verify_command": "research",
            "arithmetic": None,
            "sourcing": None,
            "assumptions": None,
            "knowledge": None,
            "unresolved_questions": None,
            "confirm_all_pass": confirm_all_pass,
            "evidence": [],
            "summary": "",
            "json_output": True,
        }
        values.update(checks)
        return SimpleNamespace(**values)

    def begin_args(self):
        return SimpleNamespace(
            goal="Research NAS backup architecture",
            scope=["research/nas-backup"],
            constraint=[],
            done=[],
            next_action="Compare three architectures",
            alignment_pending=False,
            replace=False,
            handoff=None,
            force=False,
            budget=1400,
            no_context=False,
            json_output=True,
        )

    def test_onboarding_declares_begin_as_canonical_entry_not_work_enter(self):
        block = managed_block()
        self.assertIn("Canonical routine: `begin`", block)
        self.assertIn("Do not start routine work with `work enter`", block)
        self.assertIn("debugging or integrating the lifecycle primitive", block)

    def test_research_verification_no_args_is_read_only_guided_tray(self):
        tmp, root = self.make_root()
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_verify(root, self.research_args())
            self.assertEqual(1, rc)
            payload = json.loads(out.getvalue())
            self.assertEqual("review-required", payload["status"])
            self.assertEqual(5, len(payload["checks"]))
            self.assertIn("verify research --confirm-all-pass", " ".join(payload["guidance"]["nextCommands"]))
            self.assertIsNone(latest_verification(root))
        finally:
            tmp.cleanup()

    def test_research_confirm_all_pass_is_explicit_short_path(self):
        tmp, root = self.make_root()
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_verify(
                    root,
                    self.research_args(
                        confirm_all_pass=True,
                        summary="Reviewed all five research verification dimensions.",
                    ),
                )
            self.assertEqual(0, rc)
            payload = json.loads(out.getvalue())
            self.assertEqual("pass", payload["record"]["status"])
            self.assertTrue(all(v == "pass" for v in payload["record"]["checks"].values()))
            self.assertEqual("research-bundle", latest_verification(root)["label"])
        finally:
            tmp.cleanup()

    def test_partial_research_verification_is_rejected_with_guidance(self):
        tmp, root = self.make_root()
        try:
            out = io.StringIO()
            with contextlib.redirect_stderr(out):
                rc = main_verify(root, self.research_args(arithmetic="pass"))
            self.assertEqual(2, rc)
            self.assertIn("run `agent verify research` for the guided review", out.getvalue())
        finally:
            tmp.cleanup()

    def test_checkpoint_human_output_contains_executable_next_action(self):
        tmp, root = self.make_root(profile="general")
        try:
            start_task(root, goal="Guided closeout", scope=["lifecycle"])
            align_task(root, no_material_gaps=True, resolution="ready")
            update_task(
                root,
                add_decisions=["Use canonical next-action guidance"],
                semantic_subjects={"decisions": "lifecycle/guidance"},
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_cognition(root, self.checkpoint_args())
            self.assertEqual(1, rc)
            text = out.getvalue()
            self.assertIn("REQUIRED decision", text)
            self.assertIn("agent cognition checkpoint --promote-required", text)
            self.assertIn("agent work complete", text)
        finally:
            tmp.cleanup()

    def test_complete_semantic_failure_points_to_checkpoint_promote(self):
        tmp, root = self.make_root(profile="general")
        try:
            start_task(root, goal="Guided closeout", scope=["lifecycle"])
            align_task(root, no_material_gaps=True, resolution="ready")
            update_task(
                root,
                add_decisions=["Durable decision"],
                semantic_subjects={"decisions": "lifecycle/durable"},
            )
            with self.assertRaises(TaskStateError) as ctx:
                complete_work(root)
            message = str(ctx.exception)
            self.assertIn("agent cognition checkpoint", message)
            self.assertIn("agent cognition checkpoint --promote-required", message)
            self.assertIn("agent work complete", message)
        finally:
            tmp.cleanup()

    def test_complete_research_without_verification_points_to_review_tray(self):
        tmp, root = self.make_root(profile="research")
        try:
            start_task(root, goal="Research", scope=["research"])
            align_task(root, no_material_gaps=True, resolution="ready")
            with self.assertRaises(TaskStateError) as ctx:
                complete_work(root)
            message = str(ctx.exception)
            self.assertIn("agent verify research", message)
            self.assertIn("guided review", message)
            self.assertIn("agent work complete", message)
        finally:
            tmp.cleanup()

    def test_begin_uses_work_lifecycle_language_and_clear_empty_context(self):
        tmp, root = self.make_root(profile="research")
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_begin(root, self.begin_args())
            self.assertEqual(0, rc)
            payload = json.loads(out.getvalue())
            self.assertEqual("agent-work-lifecycle", payload["lifecycle"])
            self.assertEqual(
                ["begin", "work", "checkpoint", "complete"],
                payload["canonicalRoutine"],
            )
            self.assertEqual(payload["canonicalRoutine"], payload["ritual"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
