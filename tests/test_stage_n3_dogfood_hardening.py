from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agent_devtools.core.workspace import active_status, default_work_root, recover_stale_run_state
from agent_devtools.work import cli as work_cli
from agent_devtools.work.entry import enter_work
from agent_devtools.work.state import load_task_state, task_alignment_ready
from agent_devtools.workflow import capabilities, workflow_contract


class StageN3DogfoodHardeningTests(unittest.TestCase):
    def make_root(self, profile: str = "development"):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": profile}}),
            encoding="utf-8",
        )
        return tmp, root

    def test_stale_dead_run_state_is_recovered_automatically(self) -> None:
        tmp, root = self.make_root()
        try:
            work = default_work_root(root)
            dead_pid = 99_999_999
            (work / "run.lock").write_text(str(dead_pid) + "\n", encoding="utf-8")
            (work / "active.json").write_text(
                json.dumps({"pid": dead_pid, "status": "running"}),
                encoding="utf-8",
            )
            payload = recover_stale_run_state(root)
            self.assertTrue(payload["lockRemoved"])
            self.assertTrue(payload["activeRemoved"])
            self.assertFalse((work / "run.lock").exists())
            self.assertFalse((work / "active.json").exists())
            self.assertIsNone(active_status(root))
        finally:
            tmp.cleanup()

    def test_live_run_state_is_not_reaped(self) -> None:
        tmp, root = self.make_root()
        try:
            work = default_work_root(root)
            (work / "run.lock").write_text(str(os.getpid()) + "\n", encoding="utf-8")
            (work / "active.json").write_text(
                json.dumps({"pid": os.getpid(), "status": "running"}),
                encoding="utf-8",
            )
            payload = recover_stale_run_state(root)
            self.assertFalse(payload["lockRemoved"])
            self.assertFalse(payload["activeRemoved"])
        finally:
            tmp.cleanup()

    def test_knowledge_status_is_real_cli_not_only_advertised(self) -> None:
        tmp, root = self.make_root("general")
        try:
            args = argparse.Namespace(knowledge_command="status", json_output=True)
            output = io.StringIO()
            with redirect_stdout(output):
                code = work_cli.main_knowledge(root, args)
            self.assertEqual(0, code)
            payload = json.loads(output.getvalue())
            self.assertEqual("agent-devtools-knowledge-status", payload["format"])
            self.assertEqual(0, payload["records"])
            self.assertTrue(payload["ok"])
            advertised = {
                command
                for phase in workflow_contract(root)["phases"]
                for command in phase["commands"]
            }
            self.assertIn("knowledge status", advertised)
            self.assertTrue(capabilities(root)["commands"]["knowledge"]["status"])
        finally:
            tmp.cleanup()

    def test_new_high_level_entry_defaults_to_routine_no_gap_fast_path(self) -> None:
        tmp, root = self.make_root()
        try:
            payload = enter_work(
                root,
                goal="Fix CSS overflow in the existing card",
                include_context=False,
            )
            self.assertEqual("started", payload["action"])
            self.assertTrue(payload["alignmentReady"])
            state = load_task_state(root)
            self.assertTrue(task_alignment_ready(state))
            self.assertIn("routine fast path", state["taskAlignment"]["resolution"].lower())
        finally:
            tmp.cleanup()

    def test_alignment_pending_remains_available_for_ambiguous_new_work(self) -> None:
        tmp, root = self.make_root()
        try:
            payload = enter_work(
                root,
                goal="Redesign deployment architecture",
                alignment_pending=True,
                include_context=False,
            )
            self.assertFalse(payload["alignmentReady"])
            self.assertFalse(task_alignment_ready(load_task_state(root)))
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
