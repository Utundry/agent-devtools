from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_devtools.work.journal import append_event, events_for_task


class _TrackingConnection(sqlite3.Connection):
    closed = 0

    def close(self) -> None:
        type(self).closed += 1
        super().close()


class SemanticJournalResourceHygieneTests(unittest.TestCase):
    def test_journal_connections_are_closed_after_write_and_read(self) -> None:
        real_connect = sqlite3.connect
        _TrackingConnection.closed = 0

        def tracked_connect(*args, **kwargs):
            kwargs["factory"] = _TrackingConnection
            return real_connect(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                "agent_devtools.work.journal.sqlite3.connect",
                side_effect=tracked_connect,
            ):
                append_event(
                    root,
                    task_id="task-1",
                    kind="finding",
                    text="resource hygiene",
                    created_at_utc="2026-10-07T00:00:00Z",
                )
                self.assertEqual(1, len(events_for_task(root, "task-1")))

        self.assertEqual(2, _TrackingConnection.closed)


if __name__ == "__main__":
    unittest.main()
