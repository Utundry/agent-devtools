from __future__ import annotations

import unittest
from pathlib import Path

from agent_devtools.onboarding import managed_block
from agent_devtools.workflow import WORKFLOW_CONTRACT_VERSION, capabilities, workflow_contract


ROOT = Path(__file__).resolve().parents[1]


class R8RoutineFrictionReductionTests(unittest.TestCase):
    def test_workflow_contract_makes_discovery_pull_only(self) -> None:
        payload = workflow_contract(ROOT)
        self.assertEqual(WORKFLOW_CONTRACT_VERSION, payload["formatVersion"])
        self.assertGreaterEqual(WORKFLOW_CONTRACT_VERSION, 22)
        route = payload["routineRoute"]
        self.assertTrue(route["beginSufficientForKnownRoutine"])
        self.assertTrue(route["diagnosticsPullOnly"])
        self.assertFalse(route["manualAlignmentAfterReady"])
        self.assertIn("workflow show", route["diagnosticCommands"])
        self.assertIn("capabilities --json", route["diagnosticCommands"])

    def test_capabilities_do_not_advertise_diagnostics_as_routine(self) -> None:
        payload = capabilities(ROOT)
        self.assertFalse(payload["commands"]["workflow"]["routineRequired"])
        self.assertTrue(payload["commands"]["workflow"]["pullOnly"])
        self.assertFalse(payload["commands"]["capabilities"]["routineRequired"])
        self.assertTrue(payload["commands"]["capabilities"]["pullOnly"])
        self.assertFalse(payload["normalSurface"]["diagnosticsAreRoutine"])

    def test_managed_onboarding_does_not_require_discovery_ritual(self) -> None:
        block = managed_block()
        self.assertIn("Do not run `workflow show` or `capabilities --json` as a routine preamble", block)
        self.assertIn("If `begin` reports alignment ready, do not run `work align`", block)
        self.assertNotIn("Before substantial work, run `python devtools/agent/agent.py workflow show`", block)

    def test_readme_normal_path_is_begin_first(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("For ordinary work, do not run discovery commands as a ritual", text)
        self.assertIn('python devtools/agent/agent.py begin --goal "..."', text)


if __name__ == "__main__":
    unittest.main()
