from __future__ import annotations

import importlib.util
import os
import py_compile
import subprocess
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
    def test_same_size_version_bump_and_restore_cannot_reuse_stale_bytecode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            package = root / "agent_devtools"
            package.mkdir()
            version_file = package / "__init__.py"
            original_bytes = b'__version__ = "1.0.0"\n'
            version_file.write_bytes(original_bytes)
            old_url = builder.PINNED_URL.format(version="1.0.0")
            handoff = root / "AGENT-START-HERE.md"
            handoff.write_text(old_url)
            public = root / "VERSION"
            public.write_text("1.0.0\n")
            frozen_time = 1700000000
            os.utime(version_file, (frozen_time, frozen_time))
            py_compile.compile(str(version_file), doraise=True)
            original = builder.VERSION_FILE, builder.HANDOFF, builder.PUBLIC_VERSION
            try:
                builder.VERSION_FILE, builder.HANDOFF, builder.PUBLIC_VERSION = version_file, handoff, public
                builder._set_version("1.0.0", "1.0.1")
                os.utime(version_file, (frozen_time, frozen_time))
                command = [sys.executable, "-c", "import agent_devtools; print(agent_devtools.__version__)"]
                self.assertEqual("1.0.1", subprocess.check_output(command, cwd=root, text=True).strip())
                py_compile.compile(str(version_file), doraise=True)
                builder._restore({version_file: original_bytes})
                os.utime(version_file, (frozen_time, frozen_time))
                self.assertEqual("1.0.0", subprocess.check_output(command, cwd=root, text=True).strip())
            finally:
                builder.VERSION_FILE, builder.HANDOFF, builder.PUBLIC_VERSION = original

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

    def test_release_version_rewrites_runtime_handoff_and_version_file_without_readme(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            version_file = root / "agent_devtools" / "__init__.py"
            readme = root / "README.md"
            handoff = root / "AGENT-START-HERE.md"
            public_version = root / "VERSION"
            version_file.parent.mkdir(parents=True)
            version_file.write_text('__version__ = "0.8.1"\n', encoding="utf-8")
            old_url = builder.PINNED_URL.format(version="0.8.1")
            readme.write_text(
                "# Agent DevTools\n\nHuman-facing landing page without a release version.\n",
                encoding="utf-8",
            )
            original_readme = readme.read_bytes()
            handoff.write_text(old_url + "\n", encoding="utf-8")
            public_version.write_text("0.8.1\n", encoding="utf-8")

            original = (
                builder.VERSION_FILE, builder.HANDOFF, builder.PUBLIC_VERSION
            )
            try:
                builder.VERSION_FILE = version_file
                builder.HANDOFF = handoff
                builder.PUBLIC_VERSION = public_version
                builder._set_version("0.8.1", "0.8.2")
            finally:
                (
                    builder.VERSION_FILE, builder.HANDOFF, builder.PUBLIC_VERSION
                ) = original

            self.assertIn('__version__ = "0.8.2"', version_file.read_text(encoding="utf-8"))
            new_url = builder.PINNED_URL.format(version="0.8.2")
            self.assertIn(new_url, handoff.read_text(encoding="utf-8"))
            self.assertEqual("0.8.2\n", public_version.read_text(encoding="utf-8"))
            self.assertEqual(original_readme, readme.read_bytes())
            self.assertNotIn("README.md", {path.name for path in builder.MANAGED_FILES})

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


    def test_changed_paths_preserves_porcelain_leading_space(self) -> None:
        import shutil
        import subprocess
        if not shutil.which("git"):
            self.skipTest("git is required")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git","init","-q","-b","main",str(root)], check=True)
            subprocess.run(["git","-C",str(root),"config","user.name","Test"], check=True)
            subprocess.run(["git","-C",str(root),"config","user.email","test@example.invalid"], check=True)
            path = root / "AGENT-START-HERE.md"
            path.write_text("before\n", encoding="utf-8")
            subprocess.run(["git","-C",str(root),"add","AGENT-START-HERE.md"], check=True)
            subprocess.run(["git","-C",str(root),"commit","-q","-m","base"], check=True)
            path.write_text("after\n", encoding="utf-8")
            original = builder.ROOT
            try:
                builder.ROOT = root
                self.assertEqual(("AGENT-START-HERE.md",), builder._changed_paths())
            finally:
                builder.ROOT = original


if __name__ == "__main__":
    unittest.main()
