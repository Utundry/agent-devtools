from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_devtools.check.config import CheckConfigError, load_check_config
from agent_devtools.check.policy import load_policy
from agent_devtools.check.runner import run_plan
from agent_devtools.presets import get_preset


class StageA4Harness:
    @staticmethod
    def write_project(root: Path, command: dict, *, suite: str = "check") -> None:
        (root / "agent-check.policy.json").write_text(json.dumps({
            "format": "agent-devtools-check-policy",
            "formatVersion": 1,
            "features": {"source": {"affects": [suite]}, suite: {"affects": []}},
            "sources": [{"patterns": ["src/**", "agent-tools.json"], "impact": ["source"]}],
            "suites": {suite: {"impact": [suite]}},
            "profiles": {
                "affected": {"selection": "affected", "suites": [suite], "always": []},
                "full": {"selection": "all", "suites": [suite], "always": []}
            }
        }), encoding="utf-8")
        (root / "agent-tools.json").write_text(json.dumps({
            "version": 1,
            "ignore": [".agent-cache/**", ".agent-work/**", "**/__pycache__/**"],
            "check": {
                "policy": "agent-check.policy.json",
                "suiteOrder": [suite],
                "guards": [],
                "dependencies": [],
                "commands": {suite: command}
            }
        }), encoding="utf-8")

    @staticmethod
    def run_full(root: Path, *, cache: bool = True):
        config = load_check_config(root)
        policy = load_policy(config.policy_path)
        plan = policy.plan("full", {"agent-tools.json"})
        return run_plan(root, config, plan, cache_enabled=cache)


class CompositePresetTests(unittest.TestCase):
    def test_php_vue_vite_is_composed_from_atomic_components(self) -> None:
        preset = get_preset("php-vue-vite")
        self.assertEqual((
            "context-base",
            "base",
            "php-backend",
            "vue-vite-frontend",
            "release-source",
            "release-php-runtime-backend",
            "release-vite-runtime-frontend",
        ), preset.components)
        tools = preset.files["agent-tools.json"]
        self.assertEqual([
            "backend-tests",
            "backend-composer",
            "frontend-typecheck",
            "frontend-tests",
            "frontend-build",
        ], tools["check"]["suiteOrder"])
        self.assertEqual(set(tools["check"]["suiteOrder"]), set(tools["check"]["commands"]))
        policy = preset.files["agent-check.policy.json"]
        self.assertEqual(tools["check"]["suiteOrder"], policy["profiles"]["full"]["suites"])
        self.assertIn("frontend-build", policy["suites"])
        self.assertIn("backend-tests", policy["suites"])

    def test_composite_preset_source_contains_no_copied_command_graph(self) -> None:
        path = Path(__file__).resolve().parents[1] / "presets/php-vue-vite.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual([
            "php-backend",
            "vue-vite-frontend",
            "release-source",
            "release-php-runtime-backend",
            "release-vite-runtime-frontend",
        ], raw["components"])
        self.assertEqual({}, raw["files"])


class FileSetAndArtifactContractTests(unittest.TestCase, StageA4Harness):
    def test_file_set_runs_one_command_per_matching_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src/a.py").write_text("x = 1\n", encoding="utf-8")
            (root / "src/b.py").write_text("y = 2\n", encoding="utf-8")
            self.write_project(root, {
                "argv": ["{python}", "-m", "py_compile", "{file}"],
                "inputs": ["agent-tools.json"],
                "fileSet": {"patterns": ["src/*.py"], "allowEmpty": False, "perFileTimeoutSeconds": 10},
                "tool": "python",
                "resultAdapter": "exit-code"
            })
            code, report = self.run_full(root, cache=False)
            self.assertEqual(0, code)
            result = report["checks"]["check"]["result"]
            self.assertEqual("file-set", result["execution"])
            self.assertEqual(2, result["total"])
            self.assertEqual(2, result["passed"])
            self.assertEqual({"src/a.py", "src/b.py"}, set(result["fileResults"]))

    def test_file_set_requires_file_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_project(root, {
                "argv": ["{python}", "-m", "py_compile"],
                "fileSet": {"patterns": ["src/*.py"]}
            })
            with self.assertRaises(CheckConfigError):
                load_check_config(root)

    def test_missing_required_path_fails_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_project(root, {
                "argv": ["{python}", "-c", "raise SystemExit('should not run')"],
                "requires": ["required/input.txt"],
                "resultAdapter": "exit-code"
            })
            code, report = self.run_full(root, cache=False)
            self.assertEqual(1, code)
            self.assertIn("missing required path", report["message"])

    def test_generated_output_hashes_guard_cache_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = {
                "argv": [
                    "{python}", "-c",
                    "from pathlib import Path; p=Path('dist/out.txt'); p.parent.mkdir(parents=True, exist_ok=True); p.write_text('certified', encoding='utf-8')"
                ],
                "inputs": ["agent-tools.json"],
                "tool": "python",
                "resultAdapter": "exit-code",
                "outputs": {
                    "required": ["dist/out.txt"],
                    "capture": ["dist/out.txt"]
                }
            }
            self.write_project(root, command)
            code1, report1 = self.run_full(root)
            self.assertEqual(0, code1)
            self.assertEqual("miss", report1["checks"]["check"]["cache"]["status"])
            hashes = report1["checks"]["check"]["result"]["outputs"]["hashes"]
            self.assertEqual(1, len(hashes))

            code2, report2 = self.run_full(root)
            self.assertEqual(0, code2)
            self.assertEqual("hit", report2["checks"]["check"]["cache"]["status"])

            (root / "dist/out.txt").write_text("tampered", encoding="utf-8")
            code3, report3 = self.run_full(root)
            self.assertEqual(0, code3)
            self.assertEqual("miss", report3["checks"]["check"]["cache"]["status"])
            self.assertEqual("certified", (root / "dist/out.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
