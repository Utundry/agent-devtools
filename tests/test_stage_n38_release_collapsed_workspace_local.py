from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_devtools.changes import mark_workspace_local


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "agent_devtools_build_release_n38",
    PROJECT_ROOT / "scripts" / "build_release.py",
)
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = builder
SPEC.loader.exec_module(builder)


class ReleaseCollapsedWorkspaceLocalDirectoryTests(unittest.TestCase):
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
        (root / "source.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(root), "add", "agent-tools.json", "agent-check.policy.json", "source.txt"],
            check=True,
        )
        subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "base"], check=True)
        return tmp, root

    def with_root(self, root: Path):
        class RootGuard:
            def __enter__(guard_self):
                guard_self.previous = builder.ROOT
                builder.ROOT = root
                return root

            def __exit__(guard_self, exc_type, exc, tb):
                builder.ROOT = guard_self.previous

        return RootGuard()

    def test_collapsed_untracked_directory_of_exact_workspace_local_files_is_not_material(self) -> None:
        tmp, root = self.make_repo()
        try:
            runtime = root / "devtools" / "agent"
            runtime.mkdir(parents=True)
            first = runtime / "a.py"
            second = runtime / "b.py"
            first.write_text("A\n", encoding="utf-8")
            second.write_text("B\n", encoding="utf-8")
            mark_workspace_local(
                root,
                ["devtools/agent/a.py", "devtools/agent/b.py"],
                reason="generated runtime",
            )

            with self.with_root(root):
                raw = builder._raw_changed_paths()
                self.assertIn("devtools/", raw)
                self.assertEqual((), builder._changed_paths())
                builder._git_clean()

                (root / "source.txt").write_text("material\n", encoding="utf-8")
                self.assertEqual(("source.txt",), builder._changed_paths())
                with self.assertRaises(builder.ReleaseBuilderError):
                    builder._git_clean()
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
