from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_devtools.check.adapters import evaluate_suite_result
from agent_devtools.check.config import load_check_config
from agent_devtools.check.policy import load_policy
from agent_devtools.core.process import ProcessResult
from agent_devtools.presets import PresetError, apply_preset, get_preset, list_presets


ROOT = Path(__file__).resolve().parents[1]


def fake_result(output: str, returncode: int = 0) -> ProcessResult:
    return ProcessResult(
        stage="test",
        returncode=returncode,
        duration_seconds=0.01,
        log_path=ROOT / "unused.log",
        output_tail=output,
        timed_out=False,
    )


class StructuredAdapterTests(unittest.TestCase):
    def test_unittest_adapter_extracts_test_count(self) -> None:
        evaluation = evaluate_suite_result(
            fake_result("Ran 17 tests in 0.123s\n\nOK\n"),
            root=ROOT,
            kind="unittest",
            options={"requireCount": True},
        )
        self.assertTrue(evaluation.accepted)
        self.assertEqual(17, evaluation.result["tests"])

    def test_json_line_adapter_accepts_structured_pass(self) -> None:
        output = (
            "1112 checks passed.\n"
            'AGENT_CHECK_RESULT {"format":"example-app-test-result","passed":1112,"failed":0,'
            '"failures":[],"groups":{"reference":{"passed":3,"failed":0}},"selectedGroups":["reference"]}\n'
        )
        evaluation = evaluate_suite_result(
            fake_result(output),
            root=ROOT,
            kind="json-line",
            options={
                "prefix": "AGENT_CHECK_RESULT ",
                "format": "example-app-test-result",
                "normalCompletionRegex": r"\d+ checks passed\.",
            },
        )
        self.assertTrue(evaluation.accepted)
        self.assertEqual(1112, evaluation.result["passed"])
        self.assertIn("reference", evaluation.result["groups"])

    def test_json_line_adapter_can_allow_declared_source_only_failure(self) -> None:
        label = "generated frontend assets are not present in source snapshot"
        output = (
            "1111 checks passed.\n"
            + "AGENT_CHECK_RESULT "
            + json.dumps({
                "format": "example-app-test-result",
                "passed": 1111,
                "failed": 1,
                "failures": [label],
            })
            + "\n"
        )
        evaluation = evaluate_suite_result(
            fake_result(output, returncode=1),
            root=ROOT,
            kind="json-line",
            options={
                "format": "example-app-test-result",
                "normalCompletionRegex": r"\d+ checks passed\.",
                "allowedFailures": [label],
                "allowNonZeroWithAllowedFailures": True,
            },
        )
        self.assertTrue(evaluation.accepted)
        self.assertEqual([label], evaluation.result["expectedFailures"])

    def test_json_line_adapter_rejects_unknown_failure(self) -> None:
        output = (
            "10 checks passed.\n"
            'AGENT_CHECK_RESULT {"format":"example-app-test-result","passed":10,"failed":1,'
            '"failures":["unexpected regression"]}\n'
        )
        evaluation = evaluate_suite_result(
            fake_result(output, returncode=1),
            root=ROOT,
            kind="json-line",
            options={
                "format": "example-app-test-result",
                "normalCompletionRegex": r"\d+ checks passed\.",
                "allowedFailures": ["known omission"],
                "allowNonZeroWithAllowedFailures": True,
            },
        )
        self.assertFalse(evaluation.accepted)
        self.assertEqual(["unexpected regression"], evaluation.result["unexpectedFailures"])

    def test_vite_adapter_checks_generated_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dist").mkdir()
            (root / "dist/index.html").write_text("ok", encoding="utf-8")
            evaluation = evaluate_suite_result(
                fake_result("✓ 195 modules transformed.\n"),
                root=root,
                kind="vite",
                options={"requiredFiles": ["dist/index.html"], "requireModules": True},
            )
            self.assertTrue(evaluation.accepted)
            self.assertEqual(195, evaluation.result["modules"])


class PresetTests(unittest.TestCase):
    def test_bundled_presets_are_declarative_and_loadable(self) -> None:
        presets = list_presets()
        ids = {item.preset_id for item in presets}
        self.assertTrue({
            "python-stdlib",
            "python-pytest",
            "node-typescript",
            "vue-vite",
            "php-phpunit",
            "php-vue-vite",
        }.issubset(ids))
        for preset in presets:
            with self.subTest(preset=preset.preset_id), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp)
                apply_preset(preset, project)
                config = load_check_config(project)
                policy = load_policy(config.policy_path)
                self.assertEqual(config.suite_order, policy.profiles["full"].suites)
                self.assertEqual(set(config.suite_order), set(config.commands))

    def test_preset_apply_refuses_to_overwrite_without_force(self) -> None:
        preset = get_preset("python-stdlib")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            apply_preset(preset, project)
            with self.assertRaises(PresetError):
                apply_preset(preset, project)
            apply_preset(preset, project, force=True)

    def test_python_placeholder_is_used_by_portable_preset(self) -> None:
        preset = get_preset("python-stdlib")
        config = preset.files["agent-tools.json"]
        argv = config["check"]["commands"]["tests"]["argv"]
        self.assertEqual("{python}", argv[0])


if __name__ == "__main__":
    unittest.main()

class AdapterConfigValidationTests(unittest.TestCase):
    def test_invalid_json_line_regex_is_rejected_during_config_load(self) -> None:
        from agent_devtools.check.config import CheckConfigError
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agent-check.policy.json").write_text(json.dumps({
                "format": "agent-devtools-check-policy",
                "formatVersion": 1,
                "features": {"source": {"affects": []}},
                "sources": [{"patterns": ["src/**"], "impact": ["source"]}],
                "suites": {"tests": {"impact": ["source"]}},
                "profiles": {
                    "affected": {"selection": "affected", "suites": ["tests"], "always": []},
                    "full": {"selection": "all", "suites": ["tests"], "always": []}
                }
            }), encoding="utf-8")
            (root / "agent-tools.json").write_text(json.dumps({
                "version": 1,
                "check": {
                    "policy": "agent-check.policy.json",
                    "suiteOrder": ["tests"],
                    "guards": [],
                    "dependencies": [],
                    "commands": {
                        "tests": {
                            "argv": ["echo", "ok"],
                            "inputs": [],
                            "resultAdapter": {"kind": "json-line", "fatalRegex": "["}
                        }
                    }
                }
            }), encoding="utf-8")
            with self.assertRaises(CheckConfigError):
                load_check_config(root)
