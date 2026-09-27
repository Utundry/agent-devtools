from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_devtools.context.affected import build_affected_briefing
from agent_devtools.context.config import load_context_config
from agent_devtools.context.index import ContextIndexError, ensure_index, validate_context
from agent_devtools.context.search import inspect_identity, query_context


class ContextRelevanceTests(unittest.TestCase):
    def write_config(self, root: Path, extra: dict | None = None) -> None:
        payload = {
            "version": 1,
            "ignore": [".agent-cache/**", ".agent-work/**", "build/**", "dist/**"],
            "context": {
                "defaultBudget": 1200,
                "candidateFiles": 12,
                "candidateChunks": 40,
                "lowPriority": ["docs/archive/**"],
                "semanticMap": "agent-context.map.json",
                "kindCaps": {"source": 0.8, "test": 0.5, "architecture": 0.5},
            },
            "sourceKinds": [
                {"glob": "docs/**", "kind": "architecture", "weight": 1.4},
                {"glob": "tests/**", "kind": "test", "weight": 1.2},
            ],
        }
        if extra:
            payload["context"].update(extra)
        (root / "agent-tools.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_natural_symbol_identity_survives_line_shift(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_config(root)
            (root / "src").mkdir()
            source = root / "src/service.py"
            source.write_text("def allocate_payment():\n    return 1\n", encoding="utf-8")
            config = load_context_config(root)
            ensure_index(config)
            identity = "symbol:src/service.py::allocate_payment"
            before = inspect_identity(config, identity)
            self.assertIsNotNone(before)
            source.write_text("# moved\n# again\n" + source.read_text(encoding="utf-8"), encoding="utf-8")
            ensure_index(config)
            after = inspect_identity(config, identity)
            self.assertIsNotNone(after)
            assert before is not None and after is not None
            self.assertEqual(before.chunk_id, after.chunk_id)
            self.assertGreater(after.start_line, before.start_line)

    def test_json_structural_identity_and_sidecar_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_config(root)
            (root / "package.json").write_text(json.dumps({"scripts": {"build": "vite build", "test": "vitest"}}, indent=2), encoding="utf-8")
            (root / "agent-context.map.json").write_text(json.dumps({
                "version": 1,
                "semanticRegions": [{
                    "id": "frontend.build.command",
                    "file": "package.json",
                    "selector": {"type": "json-pointer", "value": "/scripts/build"},
                }],
            }), encoding="utf-8")
            config = load_context_config(root)
            ensure_index(config)
            structural = inspect_identity(config, "struct:package.json#/scripts/build")
            self.assertIsNotNone(structural)
            mapped = inspect_identity(config, "frontend.build.command")
            self.assertIsNotNone(mapped)
            assert mapped is not None
            self.assertEqual("vite build\n", mapped.content)
            self.assertEqual("json-pointer:/scripts/build", mapped.selector)

    def test_semantic_map_change_reindexes_only_target(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_config(root)
            (root / "package.json").write_text(json.dumps({"scripts": {"build": "vite build", "test": "vitest"}}), encoding="utf-8")
            map_path = root / "agent-context.map.json"
            map_path.write_text(json.dumps({"version": 1, "semanticRegions": [{
                "id": "frontend.command", "file": "package.json",
                "selector": {"type": "json-pointer", "value": "/scripts/build"},
            }]}), encoding="utf-8")
            config = load_context_config(root)
            ensure_index(config)
            map_path.write_text(json.dumps({"version": 1, "semanticRegions": [{
                "id": "frontend.command", "file": "package.json",
                "selector": {"type": "json-pointer", "value": "/scripts/test"},
            }]}), encoding="utf-8")
            report = ensure_index(config)
            self.assertTrue(report["semanticMapChanged"])
            self.assertIn(report["changed"], (1, 2))  # target plus map file if it is normally indexed
            mapped = inspect_identity(config, "frontend.command")
            assert mapped is not None
            self.assertEqual("vitest\n", mapped.content)

    def test_invalid_sidecar_pointer_is_rejected_by_validate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_config(root)
            (root / "package.json").write_text("{}", encoding="utf-8")
            (root / "agent-context.map.json").write_text(json.dumps({"version": 1, "semanticRegions": [{
                "id": "missing", "file": "package.json",
                "selector": {"type": "json-pointer", "value": "/missing"},
            }]}), encoding="utf-8")
            config = load_context_config(root)
            with self.assertRaises(ContextIndexError):
                validate_context(config)

    def test_hard_exclusions_and_low_priority_penalty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_config(root)
            (root / "src").mkdir()
            (root / "docs/archive").mkdir(parents=True)
            (root / "build").mkdir()
            (root / "src/current.py").write_text("def payment_allocation():\n    return 'current payment allocation'\n", encoding="utf-8")
            (root / "docs/archive/old.md").write_text("# Payment allocation\nold payment allocation material\n", encoding="utf-8")
            (root / "build/generated.md").write_text("payment allocation generated", encoding="utf-8")
            config = load_context_config(root)
            ensure_index(config)
            report = query_context(config, "payment allocation", limit=8, budget=1000)
            self.assertFalse(any(item.path.startswith("build/") for item in report.results))
            current = next(item for item in report.results if item.path == "src/current.py")
            archived = next((item for item in report.results if item.path == "docs/archive/old.md"), None)
            if archived is not None:
                self.assertGreater(current.score, archived.score)

    def test_file_first_synopsis_keeps_content_only_match(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_config(root, {"candidateFiles": 5, "kindCaps": {"source": 1.0}})
            (root / "src").mkdir()
            for index in range(15):
                (root / f"src/a{index:02d}.py").write_text("def ordinary(): return 'nothing special'\n", encoding="utf-8")
            (root / "src/zzz.py").write_text("def ordinary(): return 'ultraviolet-capacitor-token'\n", encoding="utf-8")
            config = load_context_config(root)
            ensure_index(config)
            report = query_context(config, "ultraviolet capacitor token", limit=5, budget=800)
            self.assertTrue(any(item.path == "src/zzz.py" for item in report.results))

    def test_budget_is_hard_and_duplicate_windows_are_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_config(root, {"kindCaps": {"source": 1.0}})
            (root / "src").mkdir()
            body = "\n".join(["def payment_allocation():", *["    payment = obligation  # payment allocation" for _ in range(100)], "    return payment"])
            (root / "src/large.py").write_text(body, encoding="utf-8")
            config = load_context_config(root)
            ensure_index(config)
            report = query_context(config, "payment allocation", limit=20, budget=220)
            self.assertLessEqual(report.estimated_tokens, 220)
            self.assertTrue(report.results)
            self.assertGreater(report.omitted.get("duplicateOverlap", 0) + report.omitted.get("contextBudget", 0), 0)


class ImpactFirstContextTests(unittest.TestCase):
    def make_project(self, root: Path) -> None:
        (root / "agent-tools.json").write_text(json.dumps({
            "version": 1,
            "ignore": [".agent-cache/**", ".agent-work/**"],
            "context": {"defaultBudget": 1200, "kindCaps": {"source": 1.0, "architecture": 1.0}},
            "check": {"policy": "agent-check.policy.json", "suiteOrder": ["money-tests", "inventory-tests"], "guards": [], "dependencies": [], "commands": {}},
            "sourceKinds": [{"glob": "docs/**", "kind": "architecture", "weight": 1.4}],
        }), encoding="utf-8")
        (root / "agent-check.policy.json").write_text(json.dumps({
            "format": "agent-devtools-check-policy", "formatVersion": 1,
            "features": {"money": {"affects": ["money-tests"]}, "money-tests": {"affects": []}, "inventory": {"affects": ["inventory-tests"]}, "inventory-tests": {"affects": []}},
            "sources": [
                {"patterns": ["src/money/**"], "impact": ["money"]},
                {"patterns": ["src/inventory/**"], "impact": ["inventory"]},
                {"patterns": ["tests/money/**"], "impact": ["money-tests"]},
                {"patterns": ["tests/inventory/**"], "impact": ["inventory-tests"]},
            ],
            "suites": {"money-tests": {"impact": ["money-tests"]}, "inventory-tests": {"impact": ["inventory-tests"]}},
            "profiles": {
                "affected": {"selection": "affected", "suites": ["money-tests", "inventory-tests"], "always": []},
                "full": {"selection": "all", "suites": ["money-tests", "inventory-tests"], "always": []},
            },
        }), encoding="utf-8")
        for path in ("src/money", "src/inventory", "tests/money", "tests/inventory", "docs"):
            (root / path).mkdir(parents=True, exist_ok=True)
        (root / "src/money/service.py").write_text("from .rules import allocate\ndef pay(): return allocate()\n", encoding="utf-8")
        (root / "src/money/rules.py").write_text("def allocate(): return 'money payment allocation'\n", encoding="utf-8")
        (root / "src/inventory/service.py").write_text("def stock(): return 'inventory payment allocation unrelated'\n", encoding="utf-8")
        (root / "tests/money/test_pay.py").write_text("from src.money.service import pay\n", encoding="utf-8")
        (root / "tests/inventory/test_stock.py").write_text("inventory payment allocation\n", encoding="utf-8")
        (root / "docs/money.md").write_text("# Money payment allocation\nArchitecture invariant for payment allocation.\n", encoding="utf-8")

    def test_affected_excludes_classified_unrelated_domain_and_keeps_docs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_project(root)
            config = load_context_config(root)
            briefing = build_affected_briefing(root, config, changed_files=("src/money/service.py",), limit=12, budget=1400)
            paths = {item.path for item in briefing.results}
            self.assertTrue(briefing.impact_available)
            self.assertIn("src/inventory/service.py", briefing.excluded_paths)
            self.assertNotIn("src/inventory/service.py", paths)
            self.assertIn("docs/money.md", paths)
            self.assertIn("src/money/rules.py", briefing.preferred_paths)


class ContextPresetCompositionTests(unittest.TestCase):
    def test_complex_preset_inherits_context_base_once(self) -> None:
        from agent_devtools.presets import get_preset
        preset = get_preset("php-vue-vite")
        self.assertIn("context-base", preset.components)
        config = preset.files["agent-tools.json"]
        self.assertEqual(3500, config["context"]["defaultBudget"])
        self.assertEqual(1, config["context"]["maxGraphDepth"])
        self.assertTrue(any(row.get("kind") == "architecture" for row in config.get("sourceKinds", [])))

    def test_standalone_preset_reuses_context_component(self) -> None:
        from agent_devtools.presets import get_preset
        preset = get_preset("python-stdlib")
        self.assertIn("context-base", preset.components)
        self.assertIn("context", preset.files["agent-tools.json"])


if __name__ == "__main__":
    unittest.main()
