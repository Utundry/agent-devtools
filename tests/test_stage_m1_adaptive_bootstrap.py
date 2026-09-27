from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from agent_devtools.bootstrap import classify_intent, detect_project, detect_stack


class AdaptiveBootstrapTests(unittest.TestCase):
    def test_empty_workspace_stays_unclassified_before_human_intent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            detected = detect_project(root)
            stack = detect_stack(root)
            self.assertIsNone(detected.preset_id)
            self.assertEqual([], stack["technologies"])


    def test_single_file_bootstrap_does_not_turn_empty_workspace_into_python_project(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py").write_text("# installer\n", encoding="utf-8")
            detected = detect_project(root)
            stack = detect_stack(root)
            self.assertIsNone(detected.preset_id)
            self.assertEqual("none", detected.confidence)
            self.assertEqual([], stack["technologies"])

    def test_development_intent_with_python_stack_selects_safe_preset(self) -> None:
        result = classify_intent(
            "Develop a small local CLI tool",
            stack="Python standard library",
        )
        self.assertTrue(result.development)
        self.assertEqual("development", result.profile_id)
        self.assertEqual("python-stdlib", result.preset_id)
        self.assertFalse(result.needs_stack)

    def test_build_word_or_explicit_stack_classifies_development(self) -> None:
        result = classify_intent("Build a Python command-line tool", stack="Python stdlib")
        self.assertTrue(result.development)
        self.assertEqual("development", result.profile_id)
        self.assertEqual("python-stdlib", result.preset_id)

    def test_research_intent_does_not_accidentally_enable_development(self) -> None:
        result = classify_intent(
            "Исследовать варианты резервного электроснабжения и собрать источники"
        )
        self.assertFalse(result.development)
        self.assertEqual("research", result.profile_id)
        self.assertIsNone(result.preset_id)

    def test_existing_python_pytest_repository_is_detectable_from_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pyproject.toml").write_text(
                "[project]\nname='demo'\n[tool.pytest.ini_options]\naddopts='-q'\n",
                encoding="utf-8",
            )
            (root / "test_demo.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
            stack = detect_stack(root)
            detected = detect_project(root)
            self.assertIn("python", stack["technologies"])
            self.assertEqual("python-pytest", detected.preset_id)

    def test_recognized_unsupported_stack_is_reported_without_fake_preset(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Cargo.toml").write_text(
                '[package]\nname = "demo"\nversion = "0.1.0"\n',
                encoding="utf-8",
            )
            stack = detect_stack(root)
            detected = detect_project(root)
            self.assertEqual(["rust"], stack["technologies"])
            self.assertIsNone(detected.preset_id)

    def test_public_bootstrap_uses_english_default_prompts(self) -> None:
        kit = Path(__file__).resolve().parents[1] / "bootstrap" / "agent-devtools-bootstrap-kit.zip"
        with zipfile.ZipFile(kit, "r") as archive:
            source = archive.read(".agent-devtools-bootstrap-kit/BOOTSTRAP_AGENT_DEVTOOLS.py").decode("utf-8")
        self.assertIn("What are you planning to do or discuss in this project?", source)
        self.assertIn("What stack are you planning to use (language, framework, tests/build tools)?", source)
        self.assertNotIn("Что планируется делать или обсуждать в этом проекте?", source)
        self.assertNotIn("Какой стек планируется", source)


if __name__ == "__main__":
    unittest.main()
