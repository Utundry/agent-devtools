from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_devtools.bootstrap import _neutral_workspace_config
from agent_devtools.ui import VERBOSITY_COMPACT, VERBOSITY_VERBOSE, load_verbosity
from agent_devtools.work.cli import main_begin


class UIVerbosityTests(unittest.TestCase):
    def make_root(self, verbosity: str | None = None):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        config = {"version": 1, "work": {"profile": "general"}}
        if verbosity is not None:
            config["ui"] = {"verbosity": verbosity}
        (root / "agent-tools.json").write_text(json.dumps(config), encoding="utf-8")
        return tmp, root

    @staticmethod
    def begin_args(*, json_output: bool = False):
        return SimpleNamespace(
            goal="Improve output",
            profile=None,
            scope=["ui"],
            constraint=[],
            done=[],
            next_action="Continue",
            alignment_pending=False,
            replace=False,
            handoff=None,
            force=False,
            budget=1400,
            no_context=True,
            json_output=json_output,
        )

    def test_missing_or_invalid_verbosity_fails_soft_to_compact(self) -> None:
        tmp, root = self.make_root()
        try:
            self.assertEqual(VERBOSITY_COMPACT, load_verbosity(root))
            (root / "agent-tools.json").write_text(
                json.dumps({"version": 1, "work": {"profile": "general"}, "ui": {"verbosity": "noisy"}}),
                encoding="utf-8",
            )
            self.assertEqual(VERBOSITY_COMPACT, load_verbosity(root))
        finally:
            tmp.cleanup()

    def test_compact_begin_omits_repeated_routine_boilerplate(self) -> None:
        tmp, root = self.make_root(VERBOSITY_COMPACT)
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_begin(root, self.begin_args())
            self.assertEqual(0, rc)
            text = out.getvalue()
            self.assertIn("BEGIN: started", text)
            self.assertIn("context=off", text)
            self.assertIn("next: Continue", text)
            self.assertNotIn("normal surface:", text)
            self.assertNotIn("advanced primitives are not routine:", text)
            self.assertNotIn("canonical routine:", text)
        finally:
            tmp.cleanup()

    def test_verbose_begin_preserves_detailed_routine_output(self) -> None:
        tmp, root = self.make_root(VERBOSITY_VERBOSE)
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_begin(root, self.begin_args())
            self.assertEqual(0, rc)
            text = out.getvalue()
            self.assertIn("BEGIN: started", text)
            self.assertIn("normal surface:", text)
            self.assertIn("advanced primitives are not routine:", text)
            self.assertIn("canonical routine: work -> checkpoint -> complete", text)
        finally:
            tmp.cleanup()

    def test_json_output_remains_full_in_compact_mode(self) -> None:
        tmp, root = self.make_root(VERBOSITY_COMPACT)
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_begin(root, self.begin_args(json_output=True))
            self.assertEqual(0, rc)
            payload = json.loads(out.getvalue())
            self.assertEqual(["begin", "work", "checkpoint", "complete"], payload["canonicalRoutine"])
            self.assertIn("normalSurface", payload)
            self.assertIn("advancedPrimitives", payload["normalSurface"])
            self.assertIn("recovery", payload)
        finally:
            tmp.cleanup()

    def test_neutral_bootstrap_declares_compact_default(self) -> None:
        config = _neutral_workspace_config("research")
        self.assertEqual("compact", config["ui"]["verbosity"])


if __name__ == "__main__":
    unittest.main()
