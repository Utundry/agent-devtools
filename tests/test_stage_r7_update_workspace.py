from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent_devtools.workflow import capabilities


ROOT = Path(__file__).resolve().parents[1]


class R7UpdateWorkspaceTests(unittest.TestCase):
    def test_update_runtime_is_git_ignored_technical_state(self) -> None:
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".agent-updates/", ignore)

        config = json.loads((ROOT / "agent-tools.json").read_text(encoding="utf-8"))
        self.assertIn(".agent-updates/**", config["ignore"])

    def test_updater_uses_agent_updates_runs(self) -> None:
        text = (ROOT / "scripts" / "update_release.py").read_text(encoding="utf-8")
        self.assertIn('root / ".agent-updates" / "runs"', text)
        self.assertIn("output.mkdir(parents=True, exist_ok=True)", text)
        self.assertIn('shutil.copy2(scenario.path, run / "scenario.json")', text)
        self.assertNotIn('output = root / "build"', text)

    def test_capabilities_expose_disposable_update_workspace(self) -> None:
        payload = capabilities(ROOT)
        self.assertIn(".agent-updates/", payload["disposable"])
        update = payload["commands"]["declarativeUpdate"]
        self.assertEqual(".agent-updates/", update["technicalWorkspace"])
        self.assertFalse(update["trackedScenarios"])
        self.assertTrue(update["scenarioSnapshotPerRun"])


if __name__ == "__main__":
    unittest.main()
