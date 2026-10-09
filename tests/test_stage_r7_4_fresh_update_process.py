from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "update_release.py"
SPEC = importlib.util.spec_from_file_location("update_release_fresh_process_test", SCRIPT)
updater = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(updater)


class FreshUpdaterProcessTests(unittest.TestCase):
    def args(self, root: Path) -> argparse.Namespace:
        return argparse.Namespace(
            repo=str(root),
            branch="main",
            remote="origin",
            version=None,
            patch=None,
            scenario=None,
            auto=False,
            periodic=None,
            retry_failed=False,
            publish=True,
            result_file=None,
            suppress_report=False,
        )

    def test_fresh_scenario_uses_current_root_script(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "scripts").mkdir()
            (root / "scripts" / "update_release.py").write_text("# placeholder\n", encoding="utf-8")
            scenario = root / "scenario.json"
            scenario.write_text("{}\n", encoding="utf-8")

            def fake_run(command, cwd=None):
                self.assertEqual(str(root / "scripts" / "update_release.py"), command[1])
                self.assertIn("--scenario", command)
                self.assertIn("--suppress-report", command)
                result_path = Path(command[command.index("--result-file") + 1])
                result_path.write_text(
                    json.dumps({"status": "published", "version": "1.0.1"}),
                    encoding="utf-8",
                )
                return type("P", (), {"returncode": 0})()

            with patch.object(updater.subprocess, "run", side_effect=fake_run):
                payload = updater._run_fresh_process(
                    self.args(root),
                    root,
                    mode="scenario",
                    scenario_path=scenario,
                )
            self.assertEqual("published", payload["status"])

    def test_auto_no_longer_calls_in_memory_update(self) -> None:
        source = inspect.getsource(updater.auto_update)
        self.assertIn('mode="scenario"', source)
        self.assertNotIn("report = update(", source)

    def test_periodic_delegates_each_cycle_to_fresh_auto(self) -> None:
        source = inspect.getsource(updater.periodic_update)
        self.assertIn('mode="auto"', source)
        self.assertNotIn("report = auto_update(", source)

    def test_internal_result_channel_is_available(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"--result-file"', text)
        self.assertIn('"--suppress-report"', text)
        self.assertIn("args.result_file", text)
        self.assertIn("args.suppress_report", text)


if __name__ == "__main__":
    unittest.main()
