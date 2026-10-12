from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from agent_devtools.bootstrap import upgrade_runtime
from agent_devtools.presets import get_preset


class BootstrapConfigReconciliationTests(unittest.TestCase):
    def _write_raw_python_preset(self, root: Path, preset_id: str = "python-stdlib") -> None:
        preset = get_preset(preset_id)
        for rel, content in preset.files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")

    def test_runtime_upgrade_reconciles_exact_generated_python_layout(self) -> None:
        source_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_raw_python_preset(root)
            package = root / "app"
            package.mkdir()
            (package / "main.py").write_text("def main(): return 1\n", encoding="utf-8")

            result = upgrade_runtime(root, source_root=source_root)

            self.assertFalse(result["projectConfigPreserved"])
            self.assertEqual("reconciled", result["configReconciliation"]["status"])
            self.assertEqual(["app"], result["configReconciliation"]["pythonSourceRoots"])
            tools = json.loads((root / "agent-tools.json").read_text(encoding="utf-8"))
            tests_cmd = tools["check"]["commands"]["tests"]
            compile_cmd = tools["check"]["commands"]["compile"]
            self.assertEqual("{root}/app", tests_cmd["env"]["PYTHONPATH"])
            self.assertIn("app/**", tests_cmd["inputs"])
            self.assertIn("app", compile_cmd["argv"])

    def test_runtime_upgrade_preserves_customized_python_config(self) -> None:
        source_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_raw_python_preset(root)
            tools_path = root / "agent-tools.json"
            tools = json.loads(tools_path.read_text(encoding="utf-8"))
            tools["check"]["commands"]["tests"]["timeoutSeconds"] = 777
            tools_path.write_text(json.dumps(tools, indent=2) + "\n", encoding="utf-8")
            package = root / "app"
            package.mkdir()
            (package / "main.py").write_text("def main(): return 1\n", encoding="utf-8")
            before_tools = tools_path.read_bytes()
            before_policy = (root / "agent-check.policy.json").read_bytes()

            result = upgrade_runtime(root, source_root=source_root)

            self.assertTrue(result["projectConfigPreserved"])
            self.assertEqual("preserved", result["configReconciliation"]["status"])
            self.assertEqual(before_tools, tools_path.read_bytes())
            self.assertEqual(before_policy, (root / "agent-check.policy.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
