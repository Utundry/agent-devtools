import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_devtools.work.source_snapshot import source_snapshot
from agent_devtools.work.verification import _verification_source_state, _configured_command_relevance


class SourceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "project.py").write_text("value = 1\n")

    def test_snapshot_changes_with_source_bytes(self):
        first = source_snapshot(self.root)
        self.assertTrue(first["available"])
        (self.root / "project.py").write_text("value = 2\n")
        second = source_snapshot(self.root)
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])

    def test_ignored_runtime_does_not_change_snapshot(self):
        first = source_snapshot(self.root)["fingerprint"]
        folder = self.root / ".agent-work"
        folder.mkdir()
        (folder / "task.json").write_text('{"draft":1}')
        self.assertEqual(first, source_snapshot(self.root)["fingerprint"])

    def test_verification_fallback_and_unknown_relevance(self):
        state = _verification_source_state(self.root)
        self.assertTrue(state["available"])
        self.assertEqual(state["mode"], "directory-snapshot")
        self.assertEqual(_configured_command_relevance(self.root, ["python", "-V"], state)["status"], "unknown")

    def test_limits_fail_closed(self):
        with patch("agent_devtools.work.source_snapshot._MAX_FILES", 0):
            self.assertFalse(source_snapshot(self.root)["available"])


if __name__ == "__main__":
    unittest.main()
