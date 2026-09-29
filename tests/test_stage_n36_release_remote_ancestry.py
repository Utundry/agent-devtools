from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "agent_devtools_build_release_n36",
    PROJECT_ROOT / "scripts" / "build_release.py",
)
assert SPEC is not None and SPEC.loader is not None
build_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_release)


class ReleaseRemoteAncestryPreflightTests(unittest.TestCase):
    def test_equal_local_and_remote_is_allowed(self) -> None:
        with (
            patch.object(build_release, "_current_branch", return_value="main"),
            patch.object(build_release, "_run"),
            patch.object(build_release, "_git_head", return_value="aaa"),
            patch.object(build_release, "_capture", return_value="aaa"),
            patch.object(build_release, "_remote_tag_exists", return_value=False),
            patch.object(build_release, "_git_is_ancestor") as ancestor,
        ):
            branch, head = build_release._publish_preflight("0.8.10", "origin")
        self.assertEqual(("main", "aaa"), (branch, head))
        ancestor.assert_not_called()

    def test_fast_forward_ahead_local_branch_is_allowed(self) -> None:
        with (
            patch.object(build_release, "_current_branch", return_value="main"),
            patch.object(build_release, "_run"),
            patch.object(build_release, "_git_head", return_value="local-new"),
            patch.object(build_release, "_capture", return_value="remote-old"),
            patch.object(build_release, "_remote_tag_exists", return_value=False),
            patch.object(build_release, "_git_is_ancestor", return_value=True) as ancestor,
        ):
            branch, head = build_release._publish_preflight("0.8.10", "origin")
        self.assertEqual(("main", "local-new"), (branch, head))
        ancestor.assert_called_once_with("remote-old", "local-new")

    def test_behind_or_diverged_local_branch_is_rejected(self) -> None:
        with (
            patch.object(build_release, "_current_branch", return_value="main"),
            patch.object(build_release, "_run"),
            patch.object(build_release, "_git_head", return_value="local"),
            patch.object(build_release, "_capture", return_value="remote"),
            patch.object(build_release, "_remote_tag_exists", return_value=False),
            patch.object(build_release, "_git_is_ancestor", return_value=False),
        ):
            with self.assertRaises(build_release.ReleaseBuilderError) as ctx:
                build_release._publish_preflight("0.8.10", "origin")
        self.assertIn("not a fast-forward publication", str(ctx.exception))

    def test_remote_tag_still_blocks_after_valid_ancestry(self) -> None:
        with (
            patch.object(build_release, "_current_branch", return_value="main"),
            patch.object(build_release, "_run"),
            patch.object(build_release, "_git_head", return_value="local-new"),
            patch.object(build_release, "_capture", return_value="remote-old"),
            patch.object(build_release, "_remote_tag_exists", return_value=True),
            patch.object(build_release, "_git_is_ancestor", return_value=True),
        ):
            with self.assertRaises(build_release.ReleaseBuilderError) as ctx:
                build_release._publish_preflight("0.8.10", "origin")
        self.assertIn("remote tag v0.8.10 already exists", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
