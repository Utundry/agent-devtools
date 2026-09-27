from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_devtools.context.config import load_context_config
from agent_devtools.context.index import ContextIndexError, ensure_index, index_stats
from agent_devtools.context.search import inspect_anchor, query_index


class ContextIndexTests(unittest.TestCase):
    def make_project(self, root: Path) -> None:
        (root / "agent-tools.json").write_text(json.dumps({
            "version": 1,
            "ignore": [".agent-cache/**", ".agent-work/**", "build/**"],
            "sourceKinds": [
                {"glob": "docs/**", "kind": "architecture", "weight": 1.4},
                {"glob": "tests/**", "kind": "test", "weight": 1.2},
            ],
            "context": {
                "include": ["**/*.py", "**/*.md", "*.py", "*.md"],
                "chunkLines": 30,
                "overlapLines": 5,
            },
        }), encoding="utf-8")
        (root / "docs").mkdir()
        (root / "src").mkdir()
        (root / "docs/architecture.md").write_text(
            "# Context\n\n"
            "<!-- @semantic-begin context.index.lifecycle -->\n"
            "The context database is disposable derived state and can be rebuilt from source.\n"
            "<!-- @semantic-end context.index.lifecycle -->\n",
            encoding="utf-8",
        )
        (root / "src/example.py").write_text(
            "def payment_allocation(payment, obligation):\n"
            "    return payment, obligation\n",
            encoding="utf-8",
        )
        (root / "build").mkdir()
        (root / "build/generated.md").write_text("should not be indexed", encoding="utf-8")

    def test_cold_and_warm_ensure_are_incremental(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_project(root)
            config = load_context_config(root)
            cold = ensure_index(config)
            self.assertEqual("rebuild", cold["mode"])
            self.assertEqual(2, cold["files"])  # docs + source; JSON config is outside this test include set
            self.assertGreater(cold["chunks"], 0)
            warm = ensure_index(config)
            self.assertEqual("incremental", warm["mode"])
            self.assertEqual(0, warm["changed"])
            self.assertEqual(0, warm["removed"])

    def test_semantic_anchor_survives_line_movement(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_project(root)
            config = load_context_config(root)
            ensure_index(config)
            first = inspect_anchor(config, "context.index.lifecycle")
            self.assertIsNotNone(first)
            assert first is not None
            first_id = first.chunk_id
            path = root / "docs/architecture.md"
            path.write_text("intro\nintro2\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
            report = ensure_index(config)
            self.assertEqual(1, report["changed"])
            second = inspect_anchor(config, "context.index.lifecycle")
            self.assertIsNotNone(second)
            assert second is not None
            self.assertEqual(first_id, second.chunk_id)
            self.assertGreater(second.start_line, first.start_line)

    def test_query_returns_provenance_and_source_weight(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_project(root)
            config = load_context_config(root)
            ensure_index(config)
            results = query_index(config, "disposable derived state", limit=5, max_chars=3000)
            self.assertTrue(results)
            top = results[0]
            self.assertEqual("docs/architecture.md", top.path)
            self.assertEqual("architecture", top.source_kind)
            self.assertEqual(1.4, top.weight)
            self.assertGreaterEqual(top.start_line, 1)
            self.assertGreaterEqual(top.end_line, top.start_line)

    def test_deleted_database_is_fully_rebuildable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_project(root)
            config = load_context_config(root)
            ensure_index(config)
            before = [(r.path, r.label) for r in query_index(config, "payment allocation")]
            config.database.unlink()
            rebuilt = ensure_index(config)
            self.assertEqual("rebuild", rebuilt["mode"])
            after = [(r.path, r.label) for r in query_index(config, "payment allocation")]
            self.assertEqual(before, after)

    def test_lexical_fallback_works_without_fts5(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_project(root)
            config = load_context_config(root)
            report = ensure_index(config, force_no_fts=True)
            self.assertFalse(report["fts5"])
            results = query_index(config, "payment obligation")
            self.assertTrue(any(item.path == "src/example.py" for item in results))

    def test_documented_raw_marker_text_is_not_treated_as_an_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_project(root)
            path = root / "docs/architecture.md"
            path.write_text(
                path.read_text(encoding="utf-8")
                + "\n```text\n@semantic-begin context.index.lifecycle\n@semantic-end context.index.lifecycle\n```\n",
                encoding="utf-8",
            )
            config = load_context_config(root)
            ensure_index(config)
            item = inspect_anchor(config, "context.index.lifecycle")
            self.assertIsNotNone(item)

    def test_duplicate_explicit_anchor_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_project(root)
            (root / "docs/duplicate.md").write_text(
                "<!-- @semantic-begin context.index.lifecycle -->\nother\n<!-- @semantic-end context.index.lifecycle -->\n",
                encoding="utf-8",
            )
            config = load_context_config(root)
            with self.assertRaises(ContextIndexError):
                ensure_index(config)

    def test_stats_do_not_build_missing_index(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.make_project(root)
            config = load_context_config(root)
            stats = index_stats(config)
            self.assertFalse(stats["exists"])
            self.assertFalse(config.database.exists())


if __name__ == "__main__":
    unittest.main()
