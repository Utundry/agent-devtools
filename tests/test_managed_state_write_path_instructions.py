from __future__ import annotations

import unittest

from agent_devtools.onboarding import managed_block


class ManagedStateWritePathInstructionsTests(unittest.TestCase):
    def test_agents_managed_block_requires_canonical_tool_write_paths(self) -> None:
        block = managed_block()
        self.assertIn("Managed-state write path", block)
        self.assertIn("use its canonical CLI/API", block)
        self.assertIn("direct writes are reserved for explicit migration/repair paths", block)
        self.assertIn("active task state", block)
        self.assertIn("durable knowledge", block)
        self.assertIn("verification evidence", block)
        self.assertIn("update/release state", block)
        self.assertIn("Do not use `task update` for normal findings/decisions/assumptions", block)
        self.assertIn("Do not use `work enter` instead of `begin`", block)
        self.assertIn("Do not use `work finish` for routine completion", block)

    def test_agents_managed_block_forbids_manual_durable_schema_reproduction(self) -> None:
        block = managed_block()
        self.assertIn("Never create `.agent-knowledge` records", block)
        self.assertIn("checkpoint/remember/promotion can perform the transition", block)


if __name__ == "__main__":
    unittest.main()
