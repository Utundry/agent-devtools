from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_devtools.context.config import load_context_config
from agent_devtools.context.index import ensure_index, index_stats
from agent_devtools.context.search import query_context


class HybridSemanticRetrievalTests(unittest.TestCase):
    def make_project(self, root: Path) -> None:
        (root / "agent-tools.json").write_text(json.dumps({
            "version": 1,
            "ignore": [".agent-cache/**", ".agent-work/**"],
            "context": {
                "include": ["**/*.json", "**/*.md", "*.json", "*.md"],
                "candidateFiles": 8,
                "candidateChunks": 40,
                "defaultBudget": 1200,
                "kindCaps": {"source": 1.0, "architecture": 1.0, "knowledge": 1.0},
            },
            "sourceKinds": [
                {"glob": "docs/**", "kind": "architecture", "weight": 1.0},
            ],
        }), encoding="utf-8")
        (root / ".agent-knowledge" / "decisions").mkdir(parents=True)
        record = {
            "format": "agent-devtools-project-knowledge",
            "formatVersion": 1,
            "id": "runtime-state-decision",
            "kind": "decision",
            "subject": "Agent runtime state ownership and Git synchronization",
            "status": "active",
            "lifecycleStatus": "active",
            "statement": "Disposable runtime state stays local to each agent while durable knowledge synchronizes through Git.",
            "scope": ["multi-agent", "durable-knowledge"],
            "anchors": ["runtime-state", "git-sync"],
            "supersedes": [],
            "sourceRefs": [],
            "changedFiles": [],
            "createdAtUtc": "2026-10-12T00:00:00Z",
        }
        (root / ".agent-knowledge" / "decisions" / "runtime-state.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (root / "docs").mkdir()
        (root / "docs" / "noisy.md").write_text(
            "# Runtime state synchronization\n"
            "runtime runtime state state synchronization synchronization generic implementation notes\n",
            encoding="utf-8",
        )

    def test_structured_knowledge_fields_outrank_noisy_lexical_source(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_project(root)
            config = load_context_config(root)
            report = ensure_index(config)
            self.assertEqual(3, index_stats(config)["schemaVersion"])
            self.assertTrue(report["fts5"])

            result = query_context(config, "runtime state synchronization multi-agent", limit=5, budget=1000)
            self.assertTrue(result.results)
            top = result.results[0]
            self.assertEqual(".agent-knowledge/decisions/runtime-state.json", top.path)
            self.assertTrue(any("knowledge subject" in why for why in top.why))
            self.assertTrue(any("knowledge scope" in why for why in top.why))
            self.assertIn("active durable knowledge", top.why)
            self.assertIn("FTS5 lexical rank", top.why)

    def test_structured_hybrid_ranking_survives_without_fts5(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_project(root)
            config = load_context_config(root)
            report = ensure_index(config, force_no_fts=True)
            self.assertFalse(report["fts5"])

            result = query_context(config, "runtime state synchronization multi-agent", limit=5, budget=1000)
            self.assertTrue(result.results)
            top = result.results[0]
            self.assertEqual(".agent-knowledge/decisions/runtime-state.json", top.path)
            self.assertTrue(any("knowledge subject" in why for why in top.why))
            self.assertTrue(any("knowledge scope" in why for why in top.why))
            self.assertNotIn("FTS5 lexical rank", top.why)

    def test_active_knowledge_outranks_rejected_equivalent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_project(root)
            rejected = {
                "format": "agent-devtools-project-knowledge",
                "formatVersion": 1,
                "id": "rejected-runtime-state",
                "kind": "decision",
                "subject": "Agent runtime state ownership and Git synchronization",
                "status": "rejected",
                "lifecycleStatus": "rejected",
                "statement": "Disposable runtime state stays local to each agent while durable knowledge synchronizes through Git.",
                "scope": ["multi-agent", "durable-knowledge"],
                "anchors": ["runtime-state"],
                "supersedes": [],
                "sourceRefs": [],
                "changedFiles": [],
                "createdAtUtc": "2026-10-12T00:00:00Z",
            }
            (root / ".agent-knowledge" / "decisions" / "rejected-runtime-state.json").write_text(
                json.dumps(rejected, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            config = load_context_config(root)
            ensure_index(config)
            result = query_context(config, "runtime state synchronization multi-agent", limit=8, budget=1400)
            matching = [item for item in result.results if item.path.startswith(".agent-knowledge/decisions/")]
            self.assertGreaterEqual(len(matching), 2)
            self.assertEqual(".agent-knowledge/decisions/runtime-state.json", matching[0].path)
            rejected_result = next(item for item in matching if item.path.endswith("rejected-runtime-state.json"))
            self.assertTrue(any("rejected durable knowledge penalty" in why for why in rejected_result.why))

    def test_incremental_reindex_refreshes_structured_knowledge(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_project(root)
            config = load_context_config(root)
            ensure_index(config)
            path = root / ".agent-knowledge" / "decisions" / "runtime-state.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["subject"] = "Workspace ownership and session locality"
            payload["scope"] = ["workspace-local", "session-state"]
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            report = ensure_index(config)
            self.assertEqual("incremental", report["mode"])
            self.assertEqual(1, report["changed"])
            result = query_context(config, "workspace ownership session locality", limit=3, budget=800)
            self.assertEqual(".agent-knowledge/decisions/runtime-state.json", result.results[0].path)
            self.assertTrue(any("knowledge subject" in why for why in result.results[0].why))


if __name__ == "__main__":
    unittest.main()
