from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_devtools.update_scenario import (
    UpdateScenarioError, load_scenario, preflight_scenarios,
)


class ScenarioReliabilityTests(unittest.TestCase):
    def test_version_is_owned_by_release_builder(self):
        for op, extra in (("write", {"content": "0.16.48\n"}),
                          ("replace", {"before": "0.16.47", "after": "0.16.48"}),
                          ("delete", {"expectedSha256": "0" * 64})):
            with self.subTest(op=op), tempfile.TemporaryDirectory() as tmp:
                file = Path(tmp) / "scenario.json"
                file.write_text(json.dumps(self.payload({"op": op, "path": "VERSION", **extra})))
                with self.assertRaisesRegex(UpdateScenarioError, "VERSION is managed"):
                    load_scenario(file)

    @staticmethod
    def payload(change):
        return {"format": "agent-devtools-update-scenario", "formatVersion": 1,
                "fromVersion": "0.16.47", "toVersion": "0.16.48",
                "changes": [change]}

    def test_unclassified_new_module_fails_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VERSION").write_text("0.16.47\n")
            (root / "agent_devtools").mkdir()
            (root / "agent_devtools" / "known.py").write_text("")
            (root / "agent-check.policy.json").write_text(json.dumps({
                "sources": [{"patterns": ["agent_devtools/known.py"], "impact": ["core"]}]
            }))
            file = root / "scenario.json"
            file.write_text(json.dumps(self.payload({
                "op": "write", "path": "agent_devtools/new_module.py", "content": "pass\n"
            })))
            with self.assertRaisesRegex(UpdateScenarioError, "unclassified top-level modules"):
                preflight_scenarios(root, [load_scenario(file)])

            (root / "agent-check.policy.json").write_text(json.dumps({
                "sources": [{"patterns": ["agent_devtools/known.py", "agent_devtools/new_module.py"], "impact": ["core"]}]
            }))
            self.assertEqual("pass", preflight_scenarios(root, [load_scenario(file)])["status"])


if __name__ == "__main__":
    unittest.main()
