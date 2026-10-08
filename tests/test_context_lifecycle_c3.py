from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from agent_devtools.work.context_projection import (
    current_context,
    last_projection_path,
    prepare_context,
    usage_path,
    why_selected,
)
from agent_devtools.work.knowledge import KNOWLEDGE_FORMAT, KNOWLEDGE_VERSION
from agent_devtools.work.state import align_task, start_task, update_task


class ContextProjectionTests(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": "general"}}),
            encoding="utf-8",
        )
        state = start_task(
            root,
            goal="Make commercial offer requisites configurable",
            scope=["documents/requisites/commercial-offer"],
        )
        align_task(root, no_material_gaps=True)
        return tmp, root, state

    def add_record(self, root: Path, record_id: str, *, kind="decision", subject="documents/requisites", statement="Use configurable requisites", scope=None, lifecycle="active", confidence="high", supersedes=None):
        status = "open" if kind in {"finding", "open_question"} else "active"
        record = {
            "format": KNOWLEDGE_FORMAT,
            "formatVersion": KNOWLEDGE_VERSION,
            "id": record_id,
            "kind": kind,
            "subject": subject,
            "status": status,
            "lifecycleStatus": lifecycle,
            "statement": statement,
            "scope": list(scope or []),
            "confidence": confidence,
            "evidenceRefs": [],
            "anchors": [],
            "supersedes": list(supersedes or []),
            "sourceRefs": [],
            "sourceSession": {},
            "changedFiles": [],
            "createdAtUtc": "2026-10-07T00:00:00Z",
            "updatedAtUtc": "2026-10-07T00:00:00Z",
        }
        path = root / ".agent-knowledge" / f"{kind}s" / f"{record_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def test_default_projection_excludes_non_active_lifecycle(self):
        tmp, root, _ = self.make_root()
        try:
            self.add_record(root, "active", scope=["documents/requisites"])
            self.add_record(root, "historical", statement="Old policy", lifecycle="historical", scope=["documents/requisites"])
            payload = prepare_context(root)
            ids = [item["id"] for item in payload["knowledge"]]
            self.assertIn("active", ids)
            self.assertNotIn("historical", ids)
            self.assertEqual(1, payload["omitted"]["lifecycle"])
        finally:
            tmp.cleanup()

    def test_resolved_semantic_knowledge_is_excluded_by_default(self):
        tmp, root, _ = self.make_root()
        try:
            path = self.add_record(root, "resolved", kind="finding", statement="Old resolved finding", scope=["documents/requisites"])
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["status"] = "resolved"
            path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            payload = prepare_context(root)
            self.assertNotIn("resolved", [item["id"] for item in payload["knowledge"]])
            self.assertEqual(1, payload["omitted"]["semanticStatus"])
        finally:
            tmp.cleanup()

    def test_hierarchical_scope_prefers_ancestor_over_unrelated_record(self):
        tmp, root, _ = self.make_root()
        try:
            self.add_record(root, "ancestor", scope=["documents/requisites"], statement="Ancestor rule")
            self.add_record(root, "other", subject="money/payments", scope=["money/payments"], statement="Payment rule")
            payload = prepare_context(root)
            self.assertEqual("ancestor", payload["knowledge"][0]["id"])
            why = " ".join(payload["knowledge"][0]["reason"])
            self.assertIn("scope:ancestor", why)
        finally:
            tmp.cleanup()

    def test_stage_changes_kind_priority_deterministically(self):
        tmp, root, _ = self.make_root()
        try:
            self.add_record(root, "question", kind="open_question", subject="question/a", statement="Which template applies?", scope=["project"])
            self.add_record(root, "evidence", kind="evidence", subject="evidence/a", statement="Regression suite passed", scope=["project"])
            planning = prepare_context(root, task="unrelated", stage="planning")
            verification = prepare_context(root, task="unrelated", stage="verification")
            self.assertLess(
                [x["id"] for x in planning["knowledge"]].index("question"),
                [x["id"] for x in planning["knowledge"]].index("evidence"),
            )
            self.assertLess(
                [x["id"] for x in verification["knowledge"]].index("evidence"),
                [x["id"] for x in verification["knowledge"]].index("question"),
            )
        finally:
            tmp.cleanup()

    def test_near_duplicates_are_suppressed_before_budgeting(self):
        tmp, root, _ = self.make_root()
        try:
            self.add_record(root, "a", statement="Commercial offer requisites use configurable field selection", scope=["documents/requisites"])
            self.add_record(root, "b", statement="Commercial offer requisites use configurable field selection everywhere", scope=["documents/requisites"])
            payload = prepare_context(root, dedup_threshold=0.60)
            ids = {item["id"] for item in payload["knowledge"]}
            self.assertEqual(1, len(ids & {"a", "b"}))
            self.assertEqual(1, payload["omitted"]["duplicateOverlap"])
        finally:
            tmp.cleanup()

    def test_projection_uses_compact_cues_with_expand_reference(self):
        tmp, root, _ = self.make_root()
        try:
            self.add_record(root, "finding", kind="finding", statement="A reusable implementation finding that should be shown compactly", scope=["documents/requisites"])
            payload = prepare_context(root, budget=256)
            item = next(x for x in payload["knowledge"] if x["id"] == "finding")
            self.assertEqual("cue", item["mode"])
            self.assertEqual("knowledge:finding", item["expandRef"])
            self.assertIn("summary", item)
        finally:
            tmp.cleanup()

    def test_current_task_projection_and_operational_next_action_are_structured(self):
        tmp, root, _ = self.make_root()
        try:
            update_task(root, add_assumptions=["Template package is valid"], add_open_questions=["Need migration?"])
            payload = current_context(root, stage="implementation")
            self.assertEqual("Make commercial offer requisites configurable", payload["task"]["currentObjective"])
            self.assertEqual(["Template package is valid"], payload["task"]["assumptions"])
            self.assertEqual(["Need migration?"], payload["task"]["openQuestions"])
            self.assertIn("verification", payload["operational"]["nextSafeAction"])
        finally:
            tmp.cleanup()

    def test_selection_reason_is_persisted_only_in_disposable_runtime_state(self):
        tmp, root, _ = self.make_root()
        try:
            self.add_record(root, "k1", scope=["documents/requisites"])
            prepare_context(root)
            why = why_selected(root, "k1")
            self.assertTrue(why["reason"])
            self.assertTrue(last_projection_path(root).is_file())
            self.assertFalse((root / ".agent-knowledge" / "last-context-projection.json").exists())
        finally:
            tmp.cleanup()

    def test_usage_tracking_is_disposable_sqlite_and_durable_knowledge_is_untouched(self):
        tmp, root, _ = self.make_root()
        try:
            path = self.add_record(root, "k1", scope=["documents/requisites"])
            before = path.read_bytes()
            prepare_context(root)
            self.assertTrue(usage_path(root).is_file())
            with closing(sqlite3.connect(usage_path(root))) as conn:
                count = conn.execute("SELECT COUNT(*) FROM knowledge_usage").fetchone()[0]
            self.assertGreaterEqual(count, 1)
            self.assertEqual(before, path.read_bytes())
            self.assertFalse((root / ".agent-work" / "knowledge.sqlite3").exists())
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
