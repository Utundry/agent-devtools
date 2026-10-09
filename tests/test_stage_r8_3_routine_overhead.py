from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_devtools.cli import main, parser
from agent_devtools.work.cli import main_cognition
from agent_devtools.work.journal import events_for_task
from agent_devtools.work.state import align_task, load_task_state, start_task
from agent_devtools.workflow import CLI_CONTRACT_VERSION, capabilities, capability_summary


class RoutineOverheadReductionTests(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": "research"}}),
            encoding="utf-8",
        )
        state = start_task(root, goal="Measure routine overhead")
        align_task(root, no_material_gaps=True, resolution="ready")
        return tmp, root, state["taskId"]

    def test_batch_cognition_preserves_distinct_typed_events_and_sources(self) -> None:
        tmp, root, task_id = self.make_root()
        try:
            payload = [
                {"kind": "finding", "text": "Finding A", "subject": "gpu/b70", "source": "vendor-doc"},
                {"kind": "decision", "text": "Decision B", "subject": "gpu/recommendation", "source": "analysis"},
                {"kind": "evidence", "text": "Benchmark C", "source": "benchmark"},
                {"kind": "observation", "text": "Local note D", "source": "inspection"},
            ]
            args = parser().parse_args(["cognition", "batch", "--stdin", "--json"])
            out = io.StringIO()
            old_stdin = __import__("sys").stdin
            __import__("sys").stdin = io.StringIO(json.dumps(payload))
            try:
                with contextlib.redirect_stdout(out):
                    rc = main_cognition(root, args)
            finally:
                __import__("sys").stdin = old_stdin
            self.assertEqual(0, rc)
            result = json.loads(out.getvalue())
            self.assertEqual(4, result["count"])
            events = events_for_task(root, task_id)
            self.assertEqual(
                ["finding", "decision", "evidence", "observation"],
                [item["kind"] for item in events],
            )
            self.assertEqual(
                ["vendor-doc", "analysis", "benchmark", "inspection"],
                [item["metadata"]["source"] for item in events],
            )
            state = load_task_state(root)
            self.assertIn("Finding A", state["findings"])
            self.assertIn("Decision B", state["decisions"])
            self.assertIn("Benchmark C", state["evidence"])
            self.assertNotIn("Local note D", state["findings"])
        finally:
            tmp.cleanup()

    def test_batch_rejects_silent_semantic_collapse(self) -> None:
        tmp, root, _ = self.make_root()
        try:
            payload = [
                {"kind": "finding", "text": "same"},
                {"kind": "finding", "text": "same"},
            ]
            args = parser().parse_args(["cognition", "batch", "--stdin"])
            old_stdin = __import__("sys").stdin
            __import__("sys").stdin = io.StringIO(json.dumps(payload))
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    rc = main_cognition(root, args)
            finally:
                __import__("sys").stdin = old_stdin
            self.assertEqual(2, rc)
            self.assertIn("must not silently collapse", out.getvalue())
            self.assertEqual([], load_task_state(root)["findings"])
        finally:
            tmp.cleanup()

    def test_single_success_output_is_one_line_and_does_not_echo_text(self) -> None:
        tmp, root, _ = self.make_root()
        try:
            args = parser().parse_args(["cognition", "finding", "sensitive long finding"])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_cognition(root, args)
            self.assertEqual(0, rc)
            lines = out.getvalue().splitlines()
            self.assertEqual(["recorded: finding"], lines)
            self.assertNotIn("sensitive long finding", out.getvalue())
        finally:
            tmp.cleanup()

    def test_passive_cli_metrics_capture_calls_stdout_and_time(self) -> None:
        tmp, root, _ = self.make_root()
        try:
            out = io.StringIO()
            with patch("agent_devtools.cli.discover_project_root", return_value=root):
                with contextlib.redirect_stdout(out):
                    rc = main(["profile", "show"])
            self.assertEqual(0, rc)
            metric_path = root / ".agent-work" / "cli-overhead.jsonl"
            rows = [json.loads(line) for line in metric_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(1, len(rows))
            self.assertEqual("profile show", rows[0]["command"])
            self.assertGreater(rows[0]["stdoutBytes"], 0)
            self.assertGreaterEqual(rows[0]["durationMs"], 0)
            self.assertEqual(0, rows[0]["exitCode"])
        finally:
            tmp.cleanup()

    def test_capabilities_advertise_overhead_reduction(self) -> None:
        tmp, root, _ = self.make_root()
        try:
            cognition = capabilities(root)["commands"]["cognition"]
            self.assertTrue(cognition["batch"])
            self.assertTrue(cognition["batchDistinctEvents"])
            self.assertTrue(cognition["oneLineSuccess"])
            metrics = capabilities(root)["commands"]["overheadMetrics"]
            self.assertTrue(metrics["passive"])
            self.assertTrue(metrics["stdoutBytes"])
            self.assertEqual(34, CLI_CONTRACT_VERSION)
            self.assertIn("cognition.batch", capability_summary(root)["releaseChanges"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
