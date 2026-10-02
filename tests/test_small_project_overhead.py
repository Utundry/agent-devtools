from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_devtools.check.cli import command_run
from agent_devtools.context.config import load_context_config
from agent_devtools.context.index import discover_files, ensure_index
from agent_devtools.core.hashing import sha256_file
from agent_devtools.work.brief import build_brief
from agent_devtools.work.knowledge import load_records, promote
from agent_devtools.work.state import start_task, update_task
from agent_devtools.work.verification import latest_verification, record_check_report


class SmallProjectOverheadTests(unittest.TestCase):
    def project(self, root: Path, **context):
        (root / "agent-tools.json").write_text(json.dumps({
            "version": 1, "ignore": [], "context": context,
            "work": {"profile": "development"},
        }))
        (root / "src").mkdir()
        (root / "src/game.py").write_text("def rotate():\n    return 1\n")

    def test_custom_ignore_cannot_accidentally_index_runtime_or_dependencies(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.project(root)
            for directory in ("devtools/agent", "nested/node_modules", "nested/.venv", ".agent-work", ".agent-cache"):
                path = root / directory
                path.mkdir(parents=True)
                (path / "noise.py").write_text("runtime_noise = 1\n")
            config = load_context_config(root)
            self.assertEqual({"agent-tools.json", "src/game.py"}, set(discover_files(config)))
            ensure_index(config)
            self.assertLess(config.database.stat().st_size, 1_000_000)
            (root / "agent-tools.json").write_text(json.dumps({"context": {"includeRuntime": True}}))
            self.assertIn("devtools/agent/noise.py", discover_files(load_context_config(root)))

    def test_self_host_source_remains_indexable(self):
        root = Path(__file__).resolve().parents[1]
        self.assertIn("agent_devtools/context/index.py", discover_files(load_context_config(root)))

    def test_brief_does_not_create_index_and_can_reuse_existing_one(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.project(root)
            start_task(root, goal="Rotate game blocks", scope=["src/game.py"])
            update_task(root, changed_files=["src/game.py"])
            brief = build_brief(root)
            self.assertFalse(brief["context"]["available"])
            config = load_context_config(root)
            self.assertFalse(config.database.exists())
            ensure_index(config)
            self.assertTrue(build_brief(root)["context"]["available"])

    def test_cli_completed_check_links_report_and_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report_dir = root / ".agent-work/runs/example"
            report_dir.mkdir(parents=True)
            report = {"status": "pass", "runDirectory": str(report_dir), "completedAtUtc": "2026-10-02T00:00:00Z"}
            report_path = report_dir / "report.json"
            report_path.write_text(json.dumps(report))
            plan = argparse.Namespace(selected=[])
            args = argparse.Namespace(max_chunks=None, time_slice_seconds=None, no_cache=False, resume=False, json_output=True)
            with patch("agent_devtools.check.cli._selection", return_value=(None, plan, None)), patch("agent_devtools.check.cli.run_plan", return_value=(0, report)):
                self.assertEqual(0, command_run(root, args))
            record = latest_verification(root)
            self.assertEqual([".agent-work/runs/example/report.json"], record["evidence"])
            self.assertEqual(sha256_file(report_path), record["reportSha256"])
            self.assertEqual(report["completedAtUtc"], record["checkCompletedAtUtc"])
            self.assertIsNone(record_check_report(root, {"status": "partial"}))
            self.assertEqual(record["id"], latest_verification(root)["id"])
            report["status"] = "fail"
            report_path.write_text(json.dumps(report))
            self.assertEqual("fail", record_check_report(root, report)["status"])

    def test_duplicate_knowledge_reuses_identity_but_explicit_successor_is_new(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            start_task(root, goal="Document rotation")
            update_task(root, add_decisions=["Use integer rotation matrices"])
            kwargs = dict(kind="decision", text="Use integer rotation matrices", subject="rotation")
            first = promote(root, **kwargs)
            self.assertEqual(first["id"], promote(root, **kwargs)["id"])
            self.assertEqual(1, len(load_records(root)))
            successor = promote(root, **kwargs, supersedes=[first["id"]])
            self.assertNotEqual(first["id"], successor["id"])
            self.assertEqual(successor["id"], promote(root, **kwargs)["id"])


if __name__ == "__main__":
    unittest.main()
