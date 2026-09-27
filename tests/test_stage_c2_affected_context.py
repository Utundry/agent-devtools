from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_devtools.context.affected import build_affected_briefing, resolve_changed
from agent_devtools.context.config import load_context_config
from agent_devtools.context.index import ContextIndexError


class AffectedContextTests(unittest.TestCase):
    def make_project(self, root: Path) -> None:
        (root / "agent-tools.json").write_text(json.dumps({
            "version": 1,
            "ignore": [".agent-cache/**", ".agent-work/**"],
            "check": {
                "policy": "agent-check.policy.json",
                "suiteOrder": ["payment-tests", "compile"],
                "guards": [],
                "dependencies": [],
                "commands": {}
            },
            "sourceKinds": [
                {"glob": "docs/**", "kind": "architecture", "weight": 1.4},
                {"glob": "tests/**", "kind": "test", "weight": 1.2}
            ]
        }), encoding="utf-8")
        (root / "agent-check.policy.json").write_text(json.dumps({
            "format": "agent-devtools-check-policy",
            "formatVersion": 1,
            "features": {
                "payment": {"affects": ["tests"]},
                "tests": {"affects": []}
            },
            "sources": [
                {"patterns": ["src/payment/**"], "impact": ["payment"]},
                {"patterns": ["tests/**"], "impact": ["tests"]}
            ],
            "suites": {
                "payment-tests": {"impact": ["tests"]},
                "compile": {"impact": ["payment"]}
            },
            "profiles": {
                "affected": {"selection": "affected", "suites": ["payment-tests", "compile"], "always": []},
                "full": {"selection": "all", "suites": ["payment-tests", "compile"], "always": []}
            }
        }), encoding="utf-8")
        (root / "src/payment").mkdir(parents=True)
        (root / "docs").mkdir()
        (root / "src/payment/allocation.py").write_text("def allocate_payment():\n    pass\n", encoding="utf-8")
        (root / "docs/payment.md").write_text("# Payment allocation\nPayments are allocated to obligations.\n", encoding="utf-8")

    def test_explicit_change_reuses_check_impact_graph(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_project(root)
            config = load_context_config(root)
            briefing = build_affected_briefing(
                root,
                config,
                changed_files=("src/payment/allocation.py",),
                limit=6,
                max_chars=5000,
            )
            self.assertTrue(briefing.impact_available)
            self.assertEqual(("payment",), briefing.initial_impact)
            self.assertEqual(("payment", "tests"), briefing.effective_impact)
            self.assertEqual(("payment-tests", "compile"), briefing.selected_suites)
            self.assertIn("payment", briefing.query)
            self.assertTrue(any(item.path == "docs/payment.md" for item in briefing.results))

    def test_unknown_change_reports_fail_safe_impact(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_project(root)
            (root / "misc.txt").write_text("unclassified", encoding="utf-8")
            config = load_context_config(root)
            briefing = build_affected_briefing(root, config, changed_files=("misc.txt",))
            self.assertTrue(briefing.fallback_full)
            self.assertIn("unknown source classification", briefing.fallback_reasons)
            self.assertEqual(("payment-tests", "compile"), briefing.selected_suites)

    def test_resolve_changed_prefers_explicit_paths_without_git(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            changed = resolve_changed(root, ["src\\payment\\allocation.py"], None)
            self.assertEqual(("src/payment/allocation.py",), changed)

    def test_resolve_changed_requires_explicit_when_git_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaises(ContextIndexError):
                resolve_changed(root, [], None)


if __name__ == "__main__":
    unittest.main()
