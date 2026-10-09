from __future__ import annotations

import unittest
from pathlib import Path

from agent_devtools.work.knowledge import effective_lifecycle_statuses, load_records


ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    "release-tooling/update-runtime-directory": "decision",
    "release-tooling/resolved-runtime-state": "decision",
    "release-tooling/recovery-state-recognition": "decision",
    "release-tooling/self-host-policy-coverage": "requirement",
    "release-tooling/auto-periodic-update": "decision",
}


class R7DurableLessonsTests(unittest.TestCase):
    def test_r7_lessons_are_valid_active_durable_knowledge(self) -> None:
        records = load_records(ROOT)
        lifecycle = effective_lifecycle_statuses(records)
        by_subject = {item["subject"]: item for item in records}
        for subject, kind in EXPECTED.items():
            self.assertIn(subject, by_subject)
            item = by_subject[subject]
            self.assertEqual(kind, item["kind"])
            self.assertEqual("active", lifecycle[item["id"]])
            self.assertTrue(item["statement"].strip())
            self.assertTrue(item.get("anchors"))
            self.assertTrue(item.get("evidenceRefs"))


if __name__ == "__main__":
    unittest.main()
