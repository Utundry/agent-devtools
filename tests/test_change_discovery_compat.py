from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_devtools.changes import discover_changes


@unittest.skipUnless(shutil.which("git"), "Git is required")
class ChangeDiscoveryCompatibilityTests(unittest.TestCase):
    def run_git(self, root: Path, *args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    def test_status_does_not_require_object_shaped_check_runner_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.run_git(root, "init", "-q")
            self.run_git(root, "config", "user.name", "Fixture")
            self.run_git(root, "config", "user.email", "fixture@example.invalid")
            (root / "agent-tools.json").write_text(json.dumps({
                "version": 1,
                "ignore": [".agent-work/**"],
                "check": "legacy-or-profile-owned",
            }), encoding="utf-8")
            (root / "source.txt").write_text("before\n", encoding="utf-8")
            self.run_git(root, "add", "agent-tools.json", "source.txt")
            self.run_git(root, "commit", "-qm", "base")
            (root / "source.txt").write_text("after\n", encoding="utf-8")
            report = discover_changes(root)
            self.assertTrue(report["available"])
            self.assertIn("source.txt", report["canonicalChangedFiles"])

    def test_change_discovery_still_honors_replay_filters_when_check_is_object(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.run_git(root, "init", "-q")
            self.run_git(root, "config", "user.name", "Fixture")
            self.run_git(root, "config", "user.email", "fixture@example.invalid")
            (root / "agent-tools.json").write_text(json.dumps({
                "version": 1,
                "ignore": [],
                "check": {"replay": {"sourceInclude": ["src/**"], "sourceExclude": ["src/generated/**"]}},
            }), encoding="utf-8")
            (root / "src/generated").mkdir(parents=True)
            (root / "src/a.txt").write_text("a\n", encoding="utf-8")
            (root / "src/generated/b.txt").write_text("b\n", encoding="utf-8")
            (root / "other.txt").write_text("o\n", encoding="utf-8")
            self.run_git(root, "add", ".")
            self.run_git(root, "commit", "-qm", "base")
            (root / "src/a.txt").write_text("aa\n", encoding="utf-8")
            (root / "src/generated/b.txt").write_text("bb\n", encoding="utf-8")
            (root / "other.txt").write_text("oo\n", encoding="utf-8")
            report = discover_changes(root)
            self.assertEqual(["src/a.txt"], report["canonicalChangedFiles"])


if __name__ == "__main__":
    unittest.main()
