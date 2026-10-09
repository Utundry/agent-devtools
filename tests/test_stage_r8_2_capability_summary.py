from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from agent_devtools.cli import parser
from agent_devtools.workflow import capability_diff, capability_summary, capabilities


ROOT = Path(__file__).resolve().parents[1]


class CapabilitySummaryTests(unittest.TestCase):
    def test_summary_is_compact_and_routine_focused(self) -> None:
        summary = capability_summary(ROOT)
        self.assertEqual("0.16.6", summary["toolVersion"])
        self.assertEqual(33, summary["cliContractVersion"])
        self.assertEqual(22, summary["workflowContractVersion"])
        self.assertLessEqual(len(summary["routineCommands"]), 5)
        self.assertFalse(summary["diagnosticsAreRoutine"])
        self.assertIn("capabilities.summary", summary["releaseChanges"])

    def test_diff_from_0165_is_semantic_not_full_payload(self) -> None:
        diff = capability_diff(ROOT, "0.16.5")
        self.assertEqual("0.16.5", diff["fromVersion"])
        self.assertEqual("0.16.6", diff["toVersion"])
        self.assertEqual({"from": 32, "to": 33}, diff["contracts"]["cli"])
        self.assertEqual([], diff["routineAdded"])
        self.assertEqual([], diff["routineRemoved"])
        self.assertIn("capabilities.diff", diff["newCapabilities"])
        self.assertNotIn("cliCommands", diff)

    def test_unknown_diff_baseline_fails_explicitly(self) -> None:
        with self.assertRaisesRegex(ValueError, "not bundled"):
            capability_diff(ROOT, "0.1.0")

    def test_cli_parser_accepts_summary_and_diff(self) -> None:
        p = parser()
        args = p.parse_args(["capabilities", "--summary"])
        self.assertTrue(args.summary)
        args = p.parse_args(["capabilities", "--diff", "0.16.5"])
        self.assertEqual("0.16.5", args.diff_version)

    def test_capabilities_advertise_compact_discovery(self) -> None:
        payload = capabilities(ROOT)["commands"]["capabilities"]
        self.assertTrue(payload["summary"])
        self.assertTrue(payload["diff"])
        self.assertTrue(payload["pullOnly"])


class SourceUpdateOnboardingRefreshTests(unittest.TestCase):
    def load_update_module(self):
        path = ROOT / "scripts" / "update_release.py"
        spec = importlib.util.spec_from_file_location("update_release_r82", path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module

    def test_refresh_invokes_fresh_updated_agent(self) -> None:
        module = self.load_update_module()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            agent = root / "agent.py"
            agent.write_text(
                "import json\n"
                "print(json.dumps({'performed':'update-managed-block'}))\n",
                encoding="utf-8",
            )
            result = module._refresh_managed_onboarding(root)
            self.assertEqual("pass", result["status"])
            self.assertEqual("update-managed-block", result["performed"])

    def test_refresh_is_nonfatal_when_agent_missing(self) -> None:
        module = self.load_update_module()
        with tempfile.TemporaryDirectory() as td:
            result = module._refresh_managed_onboarding(Path(td))
            self.assertEqual("skipped", result["status"])


if __name__ == "__main__":
    unittest.main()
