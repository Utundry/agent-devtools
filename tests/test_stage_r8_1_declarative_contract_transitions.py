from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_devtools.update_scenario import UpdateScenarioError, apply_scenario, load_scenario
from agent_devtools.workflow import capabilities


ROOT = Path(__file__).resolve().parents[1]


class DeclarativeContractTransitionTests(unittest.TestCase):
    def make_root(self, value: int = 7):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        (root / "pkg").mkdir()
        (root / "pkg" / "contract.py").write_text(
            f"PUBLIC_CONTRACT_VERSION = {value}\n",
            encoding="utf-8",
        )
        return tmp, root

    def scenario(self, root: Path, before: int, after: int, changes=None):
        payload = {
            "format": "agent-devtools-update-scenario",
            "formatVersion": 1,
            "fromVersion": "1.0.0",
            "toVersion": "1.0.1",
            "title": "contract transition",
            "contracts": {
                "public": {
                    "path": "pkg/contract.py",
                    "symbol": "PUBLIC_CONTRACT_VERSION",
                    "from": before,
                    "to": after,
                }
            },
            "changes": changes or [
                {"op": "assert_contains", "path": "VERSION", "text": "1.0.0"}
            ],
        }
        path = root / "scenario.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_contract_transition_owns_constant_update(self) -> None:
        tmp, root = self.make_root(7)
        try:
            scenario = load_scenario(self.scenario(root, 7, 8))
            self.assertEqual("public", scenario.contracts[0]["name"])
            result = apply_scenario(root, scenario)
            self.assertEqual(1, result["contractApplied"])
            self.assertIn("PUBLIC_CONTRACT_VERSION = 8", (root / "pkg" / "contract.py").read_text())
        finally:
            tmp.cleanup()

    def test_invariant_contract_is_valid_declarative_precondition(self) -> None:
        tmp, root = self.make_root(7)
        try:
            scenario = load_scenario(self.scenario(root, 7, 7))
            result = apply_scenario(root, scenario)
            self.assertEqual(0, result["contractApplied"])
            self.assertEqual(1, result["contractUnchanged"])
        finally:
            tmp.cleanup()

    def test_contract_preimage_mismatch_fails_before_changes(self) -> None:
        tmp, root = self.make_root(9)
        try:
            scenario = load_scenario(
                self.scenario(
                    root,
                    7,
                    8,
                    changes=[{"op": "write", "path": "new.txt", "content": "should-not-exist\n"}],
                )
            )
            with self.assertRaisesRegex(UpdateScenarioError, "expects 7 or already-applied 8"):
                apply_scenario(root, scenario)
            self.assertFalse((root / "new.txt").exists())
        finally:
            tmp.cleanup()

    def test_stale_historical_exact_assertion_fails_closed(self) -> None:
        tmp, root = self.make_root(7)
        try:
            (root / "tests").mkdir()
            (root / "tests" / "test_old.py").write_text(
                "import unittest\n"
                "from pkg.contract import PUBLIC_CONTRACT_VERSION\n"
                "class T(unittest.TestCase):\n"
                "    def test_old(self):\n"
                "        self.assertEqual(7, PUBLIC_CONTRACT_VERSION)\n",
                encoding="utf-8",
            )
            scenario = load_scenario(self.scenario(root, 7, 8))
            with self.assertRaisesRegex(UpdateScenarioError, "stale exact contract-version assertion"):
                apply_scenario(root, scenario)
        finally:
            tmp.cleanup()

    def test_stale_payload_field_assertion_fails_closed(self) -> None:
        tmp, root = self.make_root(7)
        try:
            (root / "tests").mkdir()
            (root / "tests" / "test_old.py").write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_old(self):\n"
                "        payload = {'publicContractVersion': 7}\n"
                "        self.assertEqual(7, payload['publicContractVersion'])\n",
                encoding="utf-8",
            )
            scenario = load_scenario(self.scenario(root, 7, 8))
            with self.assertRaisesRegex(UpdateScenarioError, "stale exact contract-version assertion"):
                apply_scenario(root, scenario)
        finally:
            tmp.cleanup()

    def test_stale_payload_get_and_exact_compare_fail_closed(self) -> None:
        for assertion in (
            "self.assertEqual(payload.get('publicContractVersion'), 7)",
            "assert payload['publicContractVersion'] == 7",
        ):
            tmp, root = self.make_root(7)
            try:
                (root / "tests").mkdir()
                (root / "tests" / "test_old.py").write_text(
                    "import unittest\n"
                    "class T(unittest.TestCase):\n"
                    "    def test_old(self):\n"
                    "        payload = {'publicContractVersion': 7}\n"
                    f"        {assertion}\n",
                    encoding="utf-8",
                )
                scenario = load_scenario(self.scenario(root, 7, 8))
                with self.assertRaisesRegex(UpdateScenarioError, "stale exact contract-version assertion"):
                    apply_scenario(root, scenario)
            finally:
                tmp.cleanup()

    def test_workflow_format_version_alias_is_guarded(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        try:
            (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
            (root / "pkg").mkdir()
            (root / "pkg" / "contract.py").write_text("WORKFLOW_CONTRACT_VERSION = 22\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_old.py").write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_old(self):\n"
                "        payload = {'formatVersion': 22}\n"
                "        self.assertEqual(22, payload['formatVersion'])\n",
                encoding="utf-8",
            )
            payload = {
                "format": "agent-devtools-update-scenario",
                "formatVersion": 1,
                "fromVersion": "1.0.0",
                "toVersion": "1.0.1",
                "contracts": {
                    "workflow": {
                        "path": "pkg/contract.py",
                        "symbol": "WORKFLOW_CONTRACT_VERSION",
                        "from": 22,
                        "to": 23,
                    }
                },
                "changes": [{"op": "assert_contains", "path": "VERSION", "text": "1.0.0"}],
            }
            scenario_path = root / "scenario.json"
            scenario_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(UpdateScenarioError, "stale exact contract-version assertion"):
                apply_scenario(root, load_scenario(scenario_path))
        finally:
            tmp.cleanup()

    def test_invalid_contract_declaration_is_rejected(self) -> None:
        tmp, root = self.make_root(7)
        try:
            payload = {
                "format": "agent-devtools-update-scenario",
                "formatVersion": 1,
                "fromVersion": "1.0.0",
                "toVersion": "1.0.1",
                "contracts": {
                    "public": {
                        "path": "pkg/contract.py",
                        "symbol": "not-a-python-name",
                        "from": 7,
                        "to": 8,
                    }
                },
                "changes": [{"op": "assert_contains", "path": "VERSION", "text": "1.0.0"}],
            }
            path = root / "bad.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(UpdateScenarioError, "invalid contract symbol"):
                load_scenario(path)
        finally:
            tmp.cleanup()

    def test_capabilities_advertise_contract_transition_support(self) -> None:
        update = capabilities(ROOT)["commands"]["declarativeUpdate"]
        self.assertTrue(update["contractTransitions"])
        self.assertTrue(update["contractConstantsOwnedByScenario"])
        self.assertTrue(update["staleExactAssertionGuard"])


if __name__ == "__main__":
    unittest.main()
