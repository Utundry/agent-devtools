from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_devtools.cli import parser
from agent_devtools.work.cli import _cognition_text, main_work
from agent_devtools.work.state import align_task, complete_task, start_task, update_task
from agent_devtools.work.verification import record_research_bundle
from agent_devtools.workflow import capabilities, workflow_contract


class BulkCaptureAndReportTests(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": "research"}}),
            encoding="utf-8",
        )
        state = start_task(
            root,
            goal="Исследовать NAS",
            definition_of_done=["Сравнить три архитектуры", "Дать рекомендацию"],
            next_step="verify research",
        )
        align_task(root, no_material_gaps=True, resolution="ready")
        update_task(
            root,
            add_findings=["Найдено: ZFS сохраняет checksum metadata"],
            add_decisions=["Рекомендовать ZFS + репликацию"],
            add_open_questions=["Проверить фактический RTO"],
            add_evidence=["vendor docs"],
        )
        record_research_bundle(
            root,
            checks={
                "arithmetic": "pass",
                "sourcing": "pass",
                "assumptions": "pass",
                "knowledge": "pass",
                "unresolved_questions": "warn",
            },
            summary="Research reviewed",
        )
        complete_task(root, summary="Исследование завершено")
        return tmp, root, state["taskId"]

    def args(self, **overrides):
        values = dict(text=None, text_option=None, stdin_input=False, from_file=None)
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_cognition_from_file_preserves_unicode_and_multiline(self):
        tmp, root, _ = self.make_root()
        try:
            path = root / "finding.md"
            path.write_text("Первая строка\nВторая строка — ZFS ✓\n", encoding="utf-8")
            text = _cognition_text(root, self.args(from_file=Path("finding.md")))
            self.assertEqual("Первая строка\nВторая строка — ZFS ✓", text)
        finally:
            tmp.cleanup()

    def test_cognition_stdin_preserves_unicode_and_multiline(self):
        tmp, root, _ = self.make_root()
        try:
            old = sys.stdin
            sys.stdin = io.StringIO("строка 1\nстрока 2 — кириллица\n")
            try:
                text = _cognition_text(root, self.args(stdin_input=True))
            finally:
                sys.stdin = old
            self.assertEqual("строка 1\nстрока 2 — кириллица", text)
        finally:
            tmp.cleanup()

    def test_exactly_one_text_source_is_required(self):
        tmp, root, _ = self.make_root()
        try:
            with self.assertRaises(Exception):
                _cognition_text(root, self.args(text="a", stdin_input=True))
            with self.assertRaises(Exception):
                _cognition_text(root, self.args())
        finally:
            tmp.cleanup()

    def test_parser_exposes_stdin_and_from_file(self):
        a = parser().parse_args(["cognition", "finding", "--stdin"])
        self.assertTrue(a.stdin_input)
        b = parser().parse_args(["cognition", "finding", "--from-file", "note.md"])
        self.assertEqual(Path("note.md"), b.from_file)

    def test_work_report_is_read_only_consolidated_projection(self):
        tmp, root, task_id = self.make_root()
        try:
            before = (root / ".agent-work" / "task.json").read_bytes()
            args = SimpleNamespace(work_command="report", json_output=True)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = main_work(root, args)
            self.assertEqual(0, rc)
            payload = json.loads(out.getvalue())
            self.assertEqual("agent-devtools-work-report", payload["format"])
            self.assertEqual(task_id, payload["taskId"])
            self.assertEqual("completed", payload["status"])
            self.assertIn("Рекомендовать ZFS", payload["decisions"][0])
            self.assertEqual("research-bundle", payload["verification"]["latest"]["label"])
            self.assertIn("knowledge", payload)
            self.assertIn("semanticJournal", payload)
            self.assertEqual(before, (root / ".agent-work" / "task.json").read_bytes())
        finally:
            tmp.cleanup()

    def test_human_work_report_has_expected_sections(self):
        tmp, root, _ = self.make_root()
        try:
            args = SimpleNamespace(work_command="report", json_output=False)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(0, main_work(root, args))
            text = out.getvalue()
            self.assertIn("WORK REPORT", text)
            self.assertIn("Goal: Исследовать NAS", text)
            self.assertIn("Decisions:", text)
            self.assertIn("Findings:", text)
            self.assertIn("Verification:", text)
            self.assertIn("Open questions:", text)
        finally:
            tmp.cleanup()

    def test_workflow_capabilities_advertise_bulk_capture_and_report(self):
        tmp, root, _ = self.make_root()
        try:
            caps = capabilities(root)
            self.assertTrue(caps["commands"]["cognition"]["stdin"])
            self.assertTrue(caps["commands"]["cognition"]["fromFile"])
            self.assertTrue(caps["commands"]["work"]["report"])
            self.assertEqual("work report", workflow_contract(root)["routineRoute"]["finalReport"])
            self.assertNotIn("work report", workflow_contract(root)["routineRoute"]["canonicalRoutine"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
