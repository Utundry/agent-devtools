from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_release_post_commit_workspace_local",
    ROOT / "scripts" / "build_release.py",
)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = builder
SPEC.loader.exec_module(builder)


class ReleasePostCommitWorkspaceLocalTests(unittest.TestCase):
    def test_post_commit_bootstrap_check_uses_allow_dirty_after_strict_git_clean(self) -> None:
        events = []

        def record_clean():
            events.append(("clean",))

        def record_run(argv):
            events.append(tuple(str(x) for x in argv))

        with (
            patch.object(builder, "_git_clean", side_effect=record_clean),
            patch.object(builder, "_run", side_effect=record_run),
        ):
            builder._post_commit_qualification("0.8.10")

        self.assertEqual(("clean",), events[0])
        self.assertIn("scripts/build_bootstrap.py", events[1])
        self.assertIn("--check", events[1])
        self.assertIn("--allow-dirty", events[1])
        self.assertIn("--expect-version", events[2])


if __name__ == "__main__":
    unittest.main()
