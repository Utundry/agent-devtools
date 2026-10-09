from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_devtools.work.context_projection import context_efficiency_status, prepare_context
from agent_devtools.work.knowledge import KNOWLEDGE_FORMAT, KNOWLEDGE_VERSION
from agent_devtools.work.state import align_task, start_task, update_task
from agent_devtools.workflow import capabilities


class ContextEfficiencyTelemetryTests(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": "research"}}),
            encoding="utf-8",
        )
        state = start_task(
            root,
            goal="Evaluate B70 inference setup",
            scope=["hardware/b70"],
        )
        align_task(root, no_material_gaps=True, resolution="ready")
        record = {
            "format": KNOWLEDGE_FORMAT,
            "formatVersion": KNOWLEDGE_VERSION,
            "id": "b70-rebar",
            "kind": "finding",
            "subject": "hardware/b70/rebar",
            "status": "confirmed",
            "lifecycleStatus": "active",
            "statement": "ReBAR availability for the B70 setup remains to be verified on hardware.",
            "scope": ["hardware/b70"],
            "confidence": "high",
            "evidenceRefs": [],
            "anchors": [],
            "supersedes": [],
            "sourceRefs": [],
            "sourceSession": {},
            "changedFiles": [],
            "createdAtUtc": "2026-10-09T00:00:00Z",
            "updatedAtUtc": "2026-10-09T00:00:00Z",
        }
        path = root / ".agent-knowledge" / "findings" / "b70-rebar.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return tmp, root, state

    def test_projection_cost_and_exact_repeat_are_recorded_passively(self):
        tmp, root, state = self.make_root()
        try:
            first = prepare_context(root, stage="orient")
            second = prepare_context(root, stage="orient")
            self.assertFalse(first["efficiency"]["repeatedProjection"])
            self.assertTrue(second["efficiency"]["repeatedProjection"])
            status = context_efficiency_status(root, state["taskId"])
            self.assertTrue(status["trackingAvailable"])
            self.assertEqual(2, status["projections"])
            self.assertEqual(1, status["repeatProjections"])
            self.assertGreater(status["primaryTokens"], 0)
            self.assertEqual(first["estimatedTokens"] + second["estimatedTokens"], status["primaryTokens"])
        finally:
            tmp.cleanup()

    def test_later_subject_bearing_cognition_is_a_conservative_reference_signal(self):
        tmp, root, state = self.make_root()
        try:
            prepare_context(root, stage="orient")
            update_task(
                root,
                add_findings=["BIOS access confirmed; ReBAR still requires a physical B70 run."],
                semantic_subjects={"findings": "hardware/b70/rebar"},
            )
            status = context_efficiency_status(root, state["taskId"])
            self.assertGreaterEqual(status["referencedPrimarySelections"], 1)
            self.assertGreater(status["primaryReferenceRate"], 0)
            self.assertEqual("later-subject-bearing-cognition", status["referenceSignal"])
            self.assertFalse(status["referenceSignalIsProofOfUse"])
        finally:
            tmp.cleanup()

    def test_unrelated_cognition_does_not_count_as_reference(self):
        tmp, root, state = self.make_root()
        try:
            prepare_context(root, stage="orient")
            update_task(
                root,
                add_findings=["Power supply sizing checked."],
                semantic_subjects={"findings": "hardware/power"},
            )
            status = context_efficiency_status(root, state["taskId"])
            self.assertEqual(0, status["referencedPrimarySelections"])
        finally:
            tmp.cleanup()

    def test_capabilities_advertise_passive_context_efficiency_without_new_routine_command(self):
        tmp, root, _ = self.make_root()
        try:
            caps = capabilities(root)
            context = caps["commands"]["context"]
            self.assertTrue(context["efficiencyTelemetry"])
            self.assertTrue(context["repeatProjectionDetection"])
            self.assertTrue(context["conservativeReferenceSignal"])
            overhead = caps["commands"]["overheadMetrics"]
            self.assertTrue(overhead["contextTokens"])
            self.assertTrue(overhead["retrievalReferenceRate"])
            self.assertTrue(overhead["noRoutineCommand"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
