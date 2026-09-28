from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_devtools.cli import _capabilities_payload, _command_inventory, _validate_workflow_commands, parser
from agent_devtools.workflow import workflow_contract


class ExecutableWorkflowContractTests(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": "development"}}),
            encoding="utf-8",
        )
        return tmp, root

    def test_real_parser_inventory_contains_workflow_paths(self) -> None:
        commands = set(_command_inventory(parser()))
        for expected in (
            "workflow show",
            "workflow validate",
            "work enter",
            "work complete",
            "knowledge status",
            "handoff resume",
            "changes status",
        ):
            self.assertIn(expected, commands)

    def test_current_workflow_contract_has_no_missing_cli_commands(self) -> None:
        tmp, root = self.make_root()
        try:
            payload = _validate_workflow_commands(workflow_contract(root), _command_inventory(parser()))
            self.assertEqual("pass", payload["status"])
            self.assertEqual([], payload["missing"])
        finally:
            tmp.cleanup()

    def test_missing_advertised_command_fails_closed(self) -> None:
        contract = {"phases": [{"commands": ["work enter", "knowledge definitely-missing"]}]}
        payload = _validate_workflow_commands(
            contract,
            ("work", "work enter", "knowledge", "knowledge status"),
        )
        self.assertEqual("fail", payload["status"])
        self.assertEqual(["knowledge definitely-missing"], payload["missing"])

    def test_capabilities_expose_parser_derived_cli_inventory(self) -> None:
        tmp, root = self.make_root()
        try:
            cli_parser = parser()
            payload = _capabilities_payload(root, cli_parser)
            self.assertEqual(list(_command_inventory(cli_parser)), payload["cliCommands"])
            self.assertTrue(payload["commands"]["workflow"]["validate"])
            self.assertIn("workflow validate", payload["cliCommands"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
