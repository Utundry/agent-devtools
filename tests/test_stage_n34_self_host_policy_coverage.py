from __future__ import annotations

import unittest
from pathlib import Path

from agent_devtools.check.policy import RESERVED_GLOBAL, RESERVED_UNKNOWN, load_policy


ROOT = Path(__file__).resolve().parents[1]


class SelfHostPolicyCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy(ROOT / "agent-check.policy.json")

    def test_every_top_level_agent_devtools_python_module_is_classified(self) -> None:
        paths = sorted(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "agent_devtools").glob("*.py")
        )
        self.assertTrue(paths)
        _mask, by_file = self.policy.classify(paths)
        unknown = [
            path for path in paths
            if RESERVED_UNKNOWN in by_file.get(path, ())
        ]
        self.assertEqual([], unknown)

    def test_known_global_facades_remain_global(self) -> None:
        paths = ("agent_devtools/cli.py", "agent_devtools/project.py")
        _mask, by_file = self.policy.classify(paths)
        for path in paths:
            self.assertIn(RESERVED_GLOBAL, by_file[path])

    def test_declarative_update_engine_is_classified_as_core(self) -> None:
        path = "agent_devtools/update_scenario.py"
        _mask, by_file = self.policy.classify((path,))
        self.assertIn("core", by_file[path])
        self.assertNotIn(RESERVED_UNKNOWN, by_file[path])
        self.assertNotIn(RESERVED_GLOBAL, by_file[path])

        plan = self.policy.plan("affected", (path,))
        self.assertFalse(plan.fallback_full)
        self.assertEqual((), plan.fallback_reasons)
        self.assertEqual(["compile", "tests"], sorted(item["id"] for item in plan.selected))

    def test_changes_and_workflow_are_classified_without_global_fallback(self) -> None:
        paths = (
            "agent_devtools/changes.py",
            "agent_devtools/changes_cli.py",
            "agent_devtools/workflow.py",
        )
        _mask, by_file = self.policy.classify(paths)
        for path in paths:
            self.assertIn("core", by_file[path])
            self.assertNotIn(RESERVED_UNKNOWN, by_file[path])
            self.assertNotIn(RESERVED_GLOBAL, by_file[path])

        plan = self.policy.plan("affected", paths)
        self.assertFalse(plan.fallback_full)
        self.assertEqual((), plan.fallback_reasons)
        self.assertEqual(["compile", "tests"], sorted(item["id"] for item in plan.selected))


if __name__ == "__main__":
    unittest.main()
