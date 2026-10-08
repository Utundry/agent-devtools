from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_devtools.work.journal import events_for_task
from agent_devtools.work.knowledge import (
    effective_lifecycle_statuses,
    explain,
    knowledge_status,
    load_records,
    promote,
    remember,
    set_lifecycle,
    supersede,
    validate_record,
)
from agent_devtools.work.semantic_closeout import semantic_checkpoint
from agent_devtools.work.sources import add_source
from agent_devtools.work.state import align_task, start_task, update_task


class DurableKnowledgeLifecycleTests(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": "general"}}),
            encoding="utf-8",
        )
        return tmp, root

    def ready(self, root: Path, *, scope=None):
        state = start_task(root, goal="Exercise durable knowledge lifecycle", scope=scope or [])
        align_task(root, no_material_gaps=True)
        return state

    def test_legacy_v1_record_loads_as_active_lifecycle(self) -> None:
        payload = {
            "format": "agent-devtools-project-knowledge",
            "formatVersion": 1,
            "id": "legacy",
            "kind": "decision",
            "subject": "documents/rendering",
            "status": "active",
            "statement": "Client-heavy rendering",
            "anchors": [],
            "supersedes": [],
            "sourceRefs": [],
            "changedFiles": [],
            "createdAtUtc": "2026-10-01T00:00:00Z",
        }
        record = validate_record(payload)
        self.assertNotIn("lifecycleStatus", record)
        self.assertNotIn("scope", record)
        self.assertNotIn("confidence", record)
        self.assertEqual("active", effective_lifecycle_statuses([record])["legacy"])

    def test_promote_enriches_tracked_file_with_scope_confidence_and_event_provenance(self) -> None:
        tmp, root = self.make_root()
        try:
            state = self.ready(root, scope=["documents", "documents/requisites"])
            text = "Requisites selection is configurable"
            update_task(
                root,
                add_decisions=[text],
                semantic_subjects={"decisions": "documents/requisites"},
            )
            event = events_for_task(root, state["taskId"])[-1]
            record = promote(
                root,
                kind="decision",
                text=text,
                subject="documents/requisites",
                confidence="high",
                evidence_refs=["check:requisites"],
            )
            path = root / ".agent-knowledge" / "decisions" / f"{record['id']}.json"
            self.assertTrue(path.is_file())
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(["documents", "documents/requisites"], raw["scope"])
            self.assertEqual("high", raw["confidence"])
            self.assertEqual(["check:requisites"], raw["evidenceRefs"])
            self.assertEqual(event["id"], raw["sourceSession"]["eventId"])
            self.assertEqual(state["taskId"], raw["sourceSession"]["taskId"])
            self.assertFalse((root / ".agent-work" / "knowledge.sqlite3").exists())
        finally:
            tmp.cleanup()

    def test_remember_promotes_subject_bearing_semantic_event(self) -> None:
        tmp, root = self.make_root()
        try:
            state = self.ready(root)
            text = "Preserve append-friendly supersession"
            update_task(
                root,
                add_findings=[text],
                semantic_subjects={"findings": "knowledge/lifecycle"},
            )
            event = events_for_task(root, state["taskId"])[-1]
            record = remember(root, event_id=event["id"], confidence="medium")
            self.assertEqual("finding", record["kind"])
            self.assertEqual("knowledge/lifecycle", record["subject"])
            self.assertEqual(event["id"], record["sourceSession"]["eventId"])
        finally:
            tmp.cleanup()

    def test_supersession_marks_old_effectively_superseded_without_rewriting_old_file(self) -> None:
        tmp, root = self.make_root()
        try:
            self.ready(root)
            first_text = "Use policy A"
            second_text = "Use policy B"
            update_task(root, add_decisions=[first_text], semantic_subjects={"decisions": "policy/render"})
            first = promote(root, kind="decision", text=first_text, subject="policy/render")
            first_path = root / ".agent-knowledge" / "decisions" / f"{first['id']}.json"
            before = first_path.read_bytes()
            update_task(root, add_decisions=[second_text], semantic_subjects={"decisions": "policy/render"})
            second = promote(root, kind="decision", text=second_text, subject="policy/render")
            supersede(root, older_id=first["id"], newer_id=second["id"])
            self.assertEqual(before, first_path.read_bytes())
            records = load_records(root)
            lifecycle = effective_lifecycle_statuses(records)
            self.assertEqual("superseded", lifecycle[first["id"]])
            self.assertEqual("active", lifecycle[second["id"]])
        finally:
            tmp.cleanup()

    def test_historical_or_rejected_record_no_longer_satisfies_semantic_closeout(self) -> None:
        for lifecycle in ("historical", "rejected"):
            with self.subTest(lifecycle=lifecycle):
                tmp, root = self.make_root()
                try:
                    self.ready(root)
                    text = "Final documents are immutable"
                    update_task(root, add_decisions=[text], semantic_subjects={"decisions": "documents/finalization"})
                    record = promote(root, kind="decision", text=text, subject="documents/finalization")
                    self.assertTrue(semantic_checkpoint(root)["clean"])
                    set_lifecycle(root, record_id=record["id"], lifecycle_status=lifecycle)
                    self.assertFalse(semantic_checkpoint(root)["clean"])
                finally:
                    tmp.cleanup()

    def test_explain_keeps_durable_provenance_even_if_disposable_journal_is_gone(self) -> None:
        tmp, root = self.make_root()
        try:
            state = self.ready(root)
            text = "Knowledge files are canonical"
            update_task(root, add_requirements=[text], semantic_subjects={"requirements": "knowledge/storage"})
            record = promote(root, kind="requirement", text=text, subject="knowledge/storage")
            journal = root / ".agent-work" / "semantic-journal.sqlite3"
            journal.unlink()
            payload = explain(root, record["id"])
            self.assertEqual(state["taskId"], payload["sourceSession"]["taskId"])
            self.assertIsNone(payload["sourceEvent"])
            self.assertEqual("active", payload["effectiveLifecycleStatus"])
        finally:
            tmp.cleanup()

    def test_status_reports_file_canonical_and_no_durable_sqlite(self) -> None:
        tmp, root = self.make_root()
        try:
            self.ready(root)
            payload = knowledge_status(root)
            self.assertEqual(".agent-knowledge", payload["canonicalStore"])
            self.assertFalse(payload["sqliteDurableStore"])
        finally:
            tmp.cleanup()

    def test_source_records_use_same_file_backed_lifecycle_shape(self) -> None:
        tmp, root = self.make_root()
        try:
            record = add_source(root, url="https://example.invalid/spec", title="Specification", claims=["Claim A"])
            self.assertEqual("active", record["lifecycleStatus"])
            self.assertEqual([], record["scope"])
            self.assertIn("updatedAtUtc", record)
            path = root / ".agent-knowledge" / "sources" / f"{record['id']}.json"
            self.assertTrue(path.is_file())
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
