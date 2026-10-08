from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_devtools.cli import parser
from agent_devtools.onboarding import managed_block
from agent_devtools.work.cli import main_cognition
from agent_devtools.work.completion import complete_work
from agent_devtools.work.journal import events_for_task
from agent_devtools.work.state import TaskStateError, align_task, load_task_state, start_task
from agent_devtools.workflow import capabilities, workflow_contract


class FailureRecoveryTests(unittest.TestCase):
    def make_root(self, *, profile: str = "research"):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": profile}}),
            encoding="utf-8",
        )
        start_task(root, goal="Finish despite tool failures", scope=["failure-recovery"])
        align_task(root, no_material_gaps=True, resolution="ready")
        return tmp, root

    def args(self, **overrides):
        values = {
            "cognition_command": "tool-failure",
            "tool": "write",
            "operation": "write report",
            "error": 'SchemaError(Missing key at ["content"])',
            "importance": "optional",
            "attempts": 2,
            "fallback": "Return the report directly to the user.",
            "json_output": True,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_parser_exposes_tool_failure_recovery(self):
        args = parser().parse_args([
            "cognition", "tool-failure",
            "--tool", "write",
            "--operation", "write report",
            "--error", "schema error",
            "--importance", "optional",
            "--attempts", "2",
            "--fallback", "return result in chat",
            "--json",
        ])
        self.assertEqual("tool-failure", args.cognition_command)
        self.assertEqual(2, args.attempts)
        self.assertEqual("optional", args.importance)

    def test_repeated_optional_failure_stops_retry_without_blocking(self):
        tmp, root = self.make_root()
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_cognition(root, self.args())
            self.assertEqual(0, rc)
            payload = json.loads(out.getvalue())
            self.assertFalse(payload["retryAllowed"])
            self.assertFalse(payload["blocking"])
            self.assertEqual("use-fallback", payload["recommendedAction"])
            self.assertEqual([], load_task_state(root)["blockers"])
            events = events_for_task(root, load_task_state(root)["taskId"])
            failure = [e for e in events if e.get("metadata", {}).get("source") == "cognition.tool-failure"]
            self.assertEqual(1, len(failure))
            self.assertTrue(failure[0]["metadata"]["retryCeilingReached"])
        finally:
            tmp.cleanup()

    def test_first_failure_allows_one_retry(self):
        tmp, root = self.make_root()
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_cognition(root, self.args(attempts=1, fallback=""))
            self.assertEqual(0, rc)
            payload = json.loads(out.getvalue())
            self.assertTrue(payload["retryAllowed"])
            self.assertEqual("retry-once", payload["recommendedAction"])
        finally:
            tmp.cleanup()

    def test_repeated_mandatory_failure_creates_blocker(self):
        tmp, root = self.make_root()
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_cognition(root, self.args(importance="mandatory"))
            self.assertEqual(1, rc)
            payload = json.loads(out.getvalue())
            self.assertTrue(payload["blocking"])
            self.assertIn("mandatory tool failure", payload["blocker"])
            self.assertIn(payload["blocker"], load_task_state(root)["blockers"])
            with self.assertRaises(TaskStateError):
                complete_work(root)
            self.assertTrue(any("resolve-blocker" in cmd for cmd in payload["nextCommands"]))
        finally:
            tmp.cleanup()

    def test_same_failure_has_stable_fingerprint(self):
        tmp, root = self.make_root()
        try:
            fingerprints = []
            for attempts in (1, 2):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    main_cognition(root, self.args(attempts=attempts))
                fingerprints.append(json.loads(out.getvalue())["fingerprint"])
            self.assertEqual(fingerprints[0], fingerprints[1])
        finally:
            tmp.cleanup()

    def test_onboarding_contract_bounds_retries_and_preserves_progress(self):
        block = managed_block()
        self.assertIn("same tool/action failure repeats twice", block)
        self.assertIn("Do not loop", block)
        self.assertIn("optional", block)
        self.assertIn("mandatory", block)
        self.assertIn("preserve already completed findings", block)

    def test_workflow_and_capabilities_advertise_progress_guarantee(self):
        tmp, root = self.make_root()
        try:
            route = workflow_contract(root)["routineRoute"]
            self.assertIn("two", route["toolFailureRecovery"])
            self.assertIn("optional", route["toolFailureRecovery"])
            caps = capabilities(root)["commands"]["cognition"]
            self.assertTrue(caps["toolFailure"])
            self.assertEqual(2, caps["toolFailureRetryCeiling"])
            self.assertTrue(caps["toolFailureMandatoryBlocks"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
