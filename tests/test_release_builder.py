from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_release", ROOT / "scripts" / "build_release.py")
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = builder
SPEC.loader.exec_module(builder)


class ReleaseBuilderTests(unittest.TestCase):
    def test_safe_public_versions(self) -> None:
        self.assertIsNotNone(builder.SAFE_VERSION.fullmatch("0.8.2"))
        self.assertIsNotNone(builder.SAFE_VERSION.fullmatch("0.9.0-rc.1"))
        self.assertIsNone(builder.SAFE_VERSION.fullmatch("../0.8.2"))

    def test_replace_once_is_fail_safe(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.txt"
            path.write_text("old old\n", encoding="utf-8")
            with self.assertRaisesRegex(builder.ReleaseBuilderError, "exactly one"):
                builder._replace_once(path, "old", "new")

    def test_release_version_rewrites_runtime_readme_handoff_and_version_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            version_file = root / "agent_devtools" / "__init__.py"
            readme = root / "README.md"
            handoff = root / "AGENT-START-HERE.md"
            public_version = root / "VERSION"
            version_file.parent.mkdir(parents=True)
            version_file.write_text('__version__ = "0.8.1"\n', encoding="utf-8")
            old_url = builder.PINNED_URL.format(version="0.8.1")
            readme.write_text(f"Current version: **0.8.1**.\n{old_url}\n", encoding="utf-8")
            handoff.write_text(old_url + "\n", encoding="utf-8")
            public_version.write_text("0.8.1\n", encoding="utf-8")

            original = (
                builder.VERSION_FILE, builder.README, builder.HANDOFF, builder.PUBLIC_VERSION
            )
            try:
                builder.VERSION_FILE = version_file
                builder.README = readme
                builder.HANDOFF = handoff
                builder.PUBLIC_VERSION = public_version
                builder._set_version("0.8.1", "0.8.2")
            finally:
                (
                    builder.VERSION_FILE, builder.README, builder.HANDOFF, builder.PUBLIC_VERSION
                ) = original

            self.assertIn('__version__ = "0.8.2"', version_file.read_text(encoding="utf-8"))
            self.assertIn("Current version: **0.8.2**.", readme.read_text(encoding="utf-8"))
            new_url = builder.PINNED_URL.format(version="0.8.2")
            self.assertIn(new_url, readme.read_text(encoding="utf-8"))
            self.assertIn(new_url, handoff.read_text(encoding="utf-8"))
            self.assertEqual("0.8.2\n", public_version.read_text(encoding="utf-8"))

    def test_snapshot_restore_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a = root / "a"
            b = root / "b"
            a.write_bytes(b"A")
            original = builder.MANAGED_FILES
            try:
                builder.MANAGED_FILES = (a, b)
                snap = builder._snapshot()
                a.write_bytes(b"changed")
                b.write_bytes(b"new")
                builder._restore(snap)
            finally:
                builder.MANAGED_FILES = original
            self.assertEqual(b"A", a.read_bytes())
            self.assertFalse(b.exists())


if __name__ == "__main__":
    unittest.main()
