from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_devtools.changes import mark_workspace_local


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "agent_devtools_build_release_n35",
    PROJECT_ROOT / "scripts" / "build_release.py",
)
assert SPEC is not None and SPEC.loader is not None
build_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_release)


class WorkspaceLocalReleasePreflightTests(unittest.TestCase):
    def make_repo(self):
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

    def with_root(self, root: Path):
        class RootGuard:
            def __enter__(guard_self):
                guard_self.previous = build_release.ROOT
                build_release.ROOT = root
                return root
            def __exit__(guard_self, exc_type, exc, tb):
                build_release.ROOT = guard_self.previous
        return RootGuard()

    def test_exact_workspace_local_dirty_tree_is_release_clean(self) -> None:
        tmp, root = self.make_repo()
        try:
            (root / ".gitignore").write_text(".agent-cache/\nlocal-only/\n", encoding="utf-8")
            (root / "AGENTS.md").write_text("generated local instructions\n", encoding="utf-8")
            mark_workspace_local(root, [".gitignore", "AGENTS.md"], reason="local")
            with self.with_root(root):
                build_release._git_clean()
                self.assertEqual((), build_release._changed_paths())
                self.assertEqual(
                    (".gitignore", "AGENTS.md"),
                    build_release._changed_paths(include_workspace_local=True),
                )
        finally:
            tmp.cleanup()

    def test_unmarked_dirty_path_still_blocks_release(self) -> None:
        tmp, root = self.make_repo()
        try:
            (root / "scratch.py").write_text("print('scratch')\n", encoding="utf-8")
            with self.with_root(root):
                with self.assertRaises(build_release.ReleaseBuilderError):
                    build_release._git_clean()
        finally:
            tmp.cleanup()

    def test_workspace_local_mark_mismatch_blocks_release(self) -> None:
        tmp, root = self.make_repo()
        try:
            (root / ".gitignore").write_text(".agent-cache/\nlocal-only/\n", encoding="utf-8")
            mark_workspace_local(root, [".gitignore"], reason="local")
            (root / ".gitignore").write_text(".agent-cache/\nlocal-only/\nreal-change/\n", encoding="utf-8")
            with self.with_root(root):
                with self.assertRaises(build_release.ReleaseBuilderError):
                    build_release._git_clean()
        finally:
            tmp.cleanup()

    def test_publish_rollback_restores_tracked_workspace_local_bytes(self) -> None:
        tmp, root = self.make_repo()
        try:
            original_head = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip()
            local_bytes = b".agent-cache/\nlocal-only/\n"
            (root / ".gitignore").write_bytes(local_bytes)
            mark_workspace_local(root, [".gitignore"], reason="local")
            with self.with_root(root):
                snapshot = build_release._workspace_local_snapshot()
                (root / "source.txt").write_text("release commit\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(root), "add", "source.txt"], check=True)
                subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "release"], check=True)
                subprocess.run(["git", "-C", str(root), "tag", "v-test"], check=True)
                build_release._rollback_local_publish(original_head, "v-test", snapshot)

            self.assertEqual(local_bytes, (root / ".gitignore").read_bytes())
            head = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip()
            self.assertEqual(original_head, head)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
