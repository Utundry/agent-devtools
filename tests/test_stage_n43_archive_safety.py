from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from agent_devtools.core.archive import ArchiveSafetyError, extract_zip_bounded, read_zip_bounded


class BoundedArchiveTests(unittest.TestCase):
    def test_duplicate_entries_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "duplicate.zip"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("x.txt", "one")
                zf.writestr("x.txt", "two")
            with self.assertRaisesRegex(ArchiveSafetyError, "duplicate archive entries"):
                read_zip_bounded(path)

    def test_entry_and_total_budgets_fail_before_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "budget.zip"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("a.txt", b"123456")
                zf.writestr("b.txt", b"abcdef")
            with self.assertRaisesRegex(ArchiveSafetyError, "entry exceeds size limit"):
                read_zip_bounded(path, max_entry_bytes=5, max_total_bytes=100)
            with self.assertRaisesRegex(ArchiveSafetyError, "uncompressed payload exceeds"):
                read_zip_bounded(path, max_entry_bytes=10, max_total_bytes=10)

    def test_safe_extraction_rejects_escape_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "escape.zip"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("../outside.txt", "bad")
            with self.assertRaisesRegex(ArchiveSafetyError, "unsafe archive path"):
                extract_zip_bounded(path, root / "out")
            self.assertFalse((root / "outside.txt").exists())

    def test_safe_extraction_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "ok.zip"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("nested/a.txt", "hello")
            out = root / "out"
            extract_zip_bounded(path, out)
            self.assertEqual("hello", (out / "nested/a.txt").read_text())


if __name__ == "__main__":
    unittest.main()
