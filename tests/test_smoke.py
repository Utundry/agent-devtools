from __future__ import annotations

import unittest

from agent_devtools.cli import _fts5_available, parser


class MigrationBaselineSmokeTests(unittest.TestCase):
    def test_cli_has_expected_facades(self) -> None:
        p = parser()
        self.assertIsNotNone(p)

    def test_sqlite_probe_returns_bool(self) -> None:
        self.assertIsInstance(_fts5_available(), bool)


if __name__ == "__main__":
    unittest.main()
