from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_devtools.onboarding import AGENTS_BEGIN, AGENTS_END, action, ensure, managed_block
from agent_devtools.workflow import workflow_contract


class CompletionResponsibilityInstructionsTests(unittest.TestCase):
    def test_managed_agents_requires_reproducible_delegation_not_pass(self):
        block = managed_block()
        self.assertIn("reproducible numbered steps", block)
        self.assertIn("user acceptance pending", block)
        self.assertIn("delegation is not evidence", block)
        self.assertIn("Zero new durable records is valid", block)

    def test_reconciles_managed_block_without_touching_local_policy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "AGENTS.md"
            p.write_text("Local instructions\n\n" + AGENTS_BEGIN + "\nold\n" + AGENTS_END + "\n\nLocal footer\n", encoding="utf-8")
            self.assertEqual("update-managed-block", action(root))
            self.assertEqual("update-managed-block", ensure(root))
            updated = p.read_text(encoding="utf-8")
            self.assertIn("Local instructions", updated)
            self.assertIn("Local footer", updated)
            self.assertIn("user acceptance pending", updated)
            self.assertEqual("unchanged", ensure(root))

    def test_workflow_teaches_review_without_new_mandatory_invocations(self):
        from agent_devtools import workflow
        source = Path(workflow.__file__).read_text(encoding="utf-8")
        self.assertIn("leave acceptance pending, never PASS", source)
        self.assertIn("zero durable additions is valid", source)
        self.assertIn('"canonicalRoutine": ["begin", "work", "checkpoint", "complete"]', source)


if __name__ == "__main__":
    unittest.main()
