from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_devtools.work.state import align_task, start_task
from agent_devtools.workflow import CLI_CONTRACT_VERSION, WORKFLOW_CONTRACT_VERSION, capabilities, workflow_contract


class RetrievalEfficiencyTests(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": "research"}}),
            encoding="utf-8",
        )
        start_task(root, goal="Research PCIe P2P for local LLM inference")
        align_task(root, no_material_gaps=True, resolution="ready")
        return tmp, root

    def test_workflow_declares_begin_as_canonical_first_pass_retrieval(self):
        tmp, root = self.make_root()
        try:
            route = workflow_contract(root)["routineRoute"]["durableRetrieval"]
            self.assertEqual("begin", route["firstPass"])
            self.assertTrue(route["trustProjectionFirst"])
            self.assertFalse(route["genericKnowledgeRescanRoutine"])
            self.assertIn("insufficient", route["fallbackRule"])
        finally:
            tmp.cleanup()

    def test_capabilities_expose_no_redundant_durable_rescan_policy(self):
        tmp, root = self.make_root()
        try:
            caps = capabilities(root)
            self.assertEqual(32, CLI_CONTRACT_VERSION)
            self.assertGreaterEqual(WORKFLOW_CONTRACT_VERSION, 21)
            context = caps["commands"]["context"]
            self.assertTrue(context["canonicalFirstPassRetrieval"])
            self.assertTrue(context["manualDurableSearchFallbackOnly"])
            self.assertTrue(context["avoidRedundantDurableRescan"])
        finally:
            tmp.cleanup()

    def test_normal_surface_does_not_add_a_new_retrieval_command(self):
        tmp, root = self.make_root()
        try:
            commands = capabilities(root)["normalSurface"]["commands"]
            self.assertIn("agent begin", commands)
            self.assertNotIn("agent knowledge search", commands)
            self.assertNotIn("rg .agent-knowledge", commands)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
