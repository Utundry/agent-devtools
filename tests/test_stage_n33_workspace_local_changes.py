from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_devtools.changes import (
    build_patch,
    discover_changes,
    mark_workspace_local,
    unmark_workspace_local,
)


class WorkspaceLocalChangeTests(unittest.TestCase):
    def make_project(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)

        (root / "agent-tools.json").write_text(json.dumps({
            "version": 1,
            "ignore": [".agent-cache/**", ".agent-work/**"],
            "check": {
                "policy": "agent-check.policy.json",
                "suiteOrder": ["tests"],
                "guards": [],
                "dependencies": [],
                "commands": {},
                "replay": {"sourceInclude": ["**"], "sourceExclude": []},
            },
        }), encoding="utf-8")
        (root / "agent-check.policy.json").write_text("{}\n", encoding="utf-8")
        (root / ".gitignore").write_text(".agent-cache/\n", encoding="utf-8")
        (root / "source.txt").write_text("base\n", encoding="utf-8")

        subprocess.run(
            ["git", "-C", str(root), "add", "agent-tools.json", "agent-check.policy.json", ".gitignore", "source.txt"],
            check=True,
        )
        subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "base"], check=True)
        return tmp, root

    def test_exact_tracked_local_mark_is_excluded_but_visible(self) -> None:
        tmp, root = self.make_project()
        try:
            (root / ".gitignore").write_text(".agent-cache/\nlocal-only/\n", encoding="utf-8")
            mark_workspace_local(root, [".gitignore"], reason="self-host local ignore")
            report = discover_changes(root)
            self.assertEqual([], report["canonicalChangedFiles"])
            self.assertEqual([".gitignore"], report["workspaceLocalChangedFiles"])
        finally:
            tmp.cleanup()

    def test_content_change_after_mark_reenters_canonical_change_set(self) -> None:
        tmp, root = self.make_project()
        try:
            (root / ".gitignore").write_text(".agent-cache/\nlocal-only/\n", encoding="utf-8")
            mark_workspace_local(root, [".gitignore"])
            (root / ".gitignore").write_text(".agent-cache/\nlocal-only/\nreal-new-rule/\n", encoding="utf-8")
            report = discover_changes(root)
            self.assertIn(".gitignore", report["canonicalChangedFiles"])
            self.assertIn(".gitignore", report["workspaceLocalMarkMismatches"])
        finally:
            tmp.cleanup()

    def test_untracked_exact_local_mark_is_excluded_until_content_changes(self) -> None:
        tmp, root = self.make_project()
        try:
            (root / "AGENTS.md").write_text("local generated contract\n", encoding="utf-8")
            mark_workspace_local(root, ["AGENTS.md"])
            self.assertNotIn("AGENTS.md", discover_changes(root)["canonicalChangedFiles"])
            (root / "AGENTS.md").write_text("changed contract\n", encoding="utf-8")
            self.assertIn("AGENTS.md", discover_changes(root)["canonicalChangedFiles"])
        finally:
            tmp.cleanup()

    def test_patch_excludes_marked_tracked_local_change(self) -> None:
        tmp, root = self.make_project()
        try:
            (root / ".gitignore").write_text(".agent-cache/\nlocal-only/\n", encoding="utf-8")
            (root / "source.txt").write_text("real source change\n", encoding="utf-8")
            mark_workspace_local(root, [".gitignore"])
            patch, report = build_patch(root)
            text = patch.decode("utf-8")
            self.assertIn("source.txt", text)
            self.assertNotIn(".gitignore", text)
            self.assertEqual(["source.txt"], report["patchFiles"])
        finally:
            tmp.cleanup()

    def test_unmark_restores_normal_change_semantics(self) -> None:
        tmp, root = self.make_project()
        try:
            (root / ".gitignore").write_text(".agent-cache/\nlocal-only/\n", encoding="utf-8")
            mark_workspace_local(root, [".gitignore"])
            unmark_workspace_local(root, [".gitignore"])
            self.assertIn(".gitignore", discover_changes(root)["canonicalChangedFiles"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
