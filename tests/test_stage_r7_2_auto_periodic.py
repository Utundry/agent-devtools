from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_release.py"
SPEC = importlib.util.spec_from_file_location("update_release_auto_periodic_test", SCRIPT)
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)


def scenario(path: Path, old: str, new: str, title: str) -> None:
    path.write_text(json.dumps({
        "format": "agent-devtools-update-scenario",
        "formatVersion": 1,
        "fromVersion": old,
        "toVersion": new,
        "title": title,
        "commitMessage": title,
        "changes": [
            {"op": "assert_contains", "path": "VERSION", "text": old}
        ],
    }), encoding="utf-8")


class AutoPeriodicUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        (self.root / ".agent-updates" / "incoming").mkdir(parents=True)

    def test_discovers_maximal_reachable_chain(self) -> None:
        incoming = self.root / ".agent-updates" / "incoming"
        scenario(incoming / "a.json", "1.0.0", "1.0.1", "a")
        scenario(incoming / "b.json", "1.0.1", "1.0.2", "b")
        scenario(incoming / "future.json", "9.0.0", "9.0.1", "future")
        self.assertEqual(
            ["a.json", "b.json"],
            [path.name for path in updater.discover_auto_chain(self.root)],
        )

    def test_ambiguous_outgoing_edge_fails_closed(self) -> None:
        incoming = self.root / ".agent-updates" / "incoming"
        scenario(incoming / "a.json", "1.0.0", "1.0.1", "a")
        scenario(incoming / "fork.json", "1.0.0", "1.0.2", "fork")
        with self.assertRaisesRegex(updater.UpdateError, "Ambiguous update chain"):
            updater.discover_auto_chain(self.root)

    def test_cycle_fails_closed(self) -> None:
        incoming = self.root / ".agent-updates" / "incoming"
        scenario(incoming / "a.json", "1.0.0", "1.0.1", "a")
        scenario(incoming / "b.json", "1.0.1", "1.0.0", "b")
        with self.assertRaisesRegex(updater.UpdateError, "cycle detected"):
            updater.discover_auto_chain(self.root)

    def test_successful_scenario_is_archived_by_digest(self) -> None:
        incoming = self.root / ".agent-updates" / "incoming"
        path = incoming / "a.json"
        scenario(path, "1.0.0", "1.0.1", "a")
        digest = updater.load_scenario(path).digest
        archived = updater._archive_applied_scenario(self.root, path, digest)
        self.assertFalse(path.exists())
        self.assertTrue(archived.is_file())
        self.assertTrue(archived.name.endswith(digest[:12] + ".json"))

    def test_periodic_failure_state_suppresses_same_catalog(self) -> None:
        incoming = self.root / ".agent-updates" / "incoming"
        scenario(incoming / "a.json", "1.0.0", "1.0.1", "a")
        key = updater._auto_catalog_key(self.root)
        updater._save_periodic_failure(self.root, key, "boom")
        self.assertEqual(key, updater._load_periodic_state(self.root)["failedKey"])
        scenario(incoming / "b.json", "2.0.0", "2.0.1", "b")
        self.assertNotEqual(key, updater._auto_catalog_key(self.root))

    def test_periodic_requires_publish_and_minimum_interval(self) -> None:
        args = argparse.Namespace(
            repo=str(self.root), branch="main", remote="origin",
            version=None, patch=None, scenario=None, auto=False,
            periodic=0.5, retry_failed=False, publish=True,
        )
        with self.assertRaisesRegex(updater.UpdateError, "at least 1 second"):
            updater.periodic_update(args, max_cycles=1)
        args.periodic = 1
        args.publish = False
        with self.assertRaisesRegex(updater.UpdateError, "requires --publish"):
            updater.periodic_update(args, max_cycles=1)

    def test_cli_source_exposes_auto_periodic_and_retry_failed(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('source.add_argument("--auto"', text)
        self.assertIn('source.add_argument("--periodic"', text)
        self.assertIn('parser.add_argument("--retry-failed"', text)
        self.assertIn("periodic_update(args)", text)
        self.assertIn("auto_update(args)", text)


if __name__ == "__main__":
    unittest.main()
