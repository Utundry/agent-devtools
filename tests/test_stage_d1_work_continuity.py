from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from agent_devtools.work.brief import build_brief, git_state, render_brief
from agent_devtools.work.checkpoint import CheckpointError, create_checkpoint, inspect_checkpoint, restore_checkpoint
from agent_devtools.work.state import clear_task, load_task_state, start_task, task_state_path, update_task


class WorkContinuityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def _init_git(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git unavailable")
        self._git("init")
        self._git("config", "user.email", "agent@example.invalid")
        self._git("config", "user.name", "Agent Tests")
        (self.root / ".gitignore").write_text(".agent-work/\n.agent-cache/\n", encoding="utf-8")
        (self.root / "tracked.txt").write_text("base\n", encoding="utf-8")
        self._git("add", ".gitignore", "tracked.txt")
        self._git("commit", "-m", "base")

    def test_task_state_is_tiny_atomic_and_incremental(self) -> None:
        state = start_task(
            self.root,
            goal="Repair payment allocation",
            scope=["money"],
            constraints=["preserve existing allocations"],
            definition_of_done=["targeted tests pass"],
            next_step="inspect PaymentService",
        )
        self.assertEqual(state["goal"], "Repair payment allocation")
        self.assertTrue(task_state_path(self.root).is_file())
        state = update_task(
            self.root,
            summary="backend path located",
            next_step="edit allocation method",
            add_scope=["obligations"],
            changed_files=["./src/.hidden.py"],
            verification=["planner inspected"],
        )
        self.assertEqual(state["scope"], ["money", "obligations"])
        self.assertEqual(state["changedFiles"], ["src/.hidden.py"])
        self.assertEqual(load_task_state(self.root)["nextStep"], "edit allocation method")
        self.assertTrue(clear_task(self.root))
        self.assertIsNone(load_task_state(self.root))

    def test_resume_recovers_goal_changes_and_interrupted_stage_without_conversation(self) -> None:
        self._init_git()
        start_task(self.root, goal="Continue interrupted work", next_step="finish frontend")
        (self.root / "tracked.txt").write_text("changed\n", encoding="utf-8")
        run = self.root / ".agent-work" / "runs" / "20260926T120000Z-check-certify-dead"
        run.mkdir(parents=True)
        (run / "report.json").write_text(json.dumps({
            "format": "agent-devtools-run",
            "formatVersion": 1,
            "status": "running",
            "mode": "check-certify",
            "pid": 99999999,
            "startedAtUtc": "2026-09-26T12:00:00Z",
            "currentStage": "frontend-build",
            "checks": {"backend": {"status": "pass"}, "frontend-build": {"status": "running"}},
        }), encoding="utf-8")
        brief = build_brief(self.root, mode="resume", include_context=False)
        self.assertTrue(brief["recovery"]["interruptedRunDetected"])
        self.assertEqual(brief["latestRun"]["currentStage"], "frontend-build")
        self.assertIn("tracked.txt", [row["path"] for row in brief["git"]["changedFiles"]])
        text = render_brief(brief)
        self.assertIn("Continue interrupted work", text)
        self.assertIn("finish frontend", text)
        self.assertIn("INTERRUPTED", text)
        self.assertIn("frontend-build", text)

    def test_checkpoint_round_trip_restores_modified_untracked_deleted_and_task(self) -> None:
        self._init_git()
        (self.root / "delete-me.txt").write_text("delete base\n", encoding="utf-8")
        self._git("add", "delete-me.txt")
        self._git("commit", "-m", "add deletion fixture")
        start_task(self.root, goal="Round trip", next_step="resume exactly")
        (self.root / "tracked.txt").write_text("modified\n", encoding="utf-8")
        (self.root / "new.bin").write_bytes(b"\x00\x01binary\xff")
        (self.root / "delete-me.txt").unlink()
        out = self.root / "saved.agent-checkpoint.zip"
        report = create_checkpoint(self.root, out=out)
        self.assertEqual(report["files"], 2)
        self.assertEqual(report["deleted"], 1)
        inspected = inspect_checkpoint(out)
        self.assertEqual(inspected["files"], 2)
        self.assertTrue(inspected["taskIncluded"])

        self._git("reset", "--hard", "HEAD")
        (self.root / "new.bin").unlink(missing_ok=True)
        task_state_path(self.root).unlink(missing_ok=True)
        restored = restore_checkpoint(self.root, out)
        self.assertEqual(restored["restoredFiles"], 2)
        self.assertEqual(restored["deletedFiles"], 1)
        self.assertEqual((self.root / "tracked.txt").read_text(), "modified\n")
        self.assertEqual((self.root / "new.bin").read_bytes(), b"\x00\x01binary\xff")
        self.assertFalse((self.root / "delete-me.txt").exists())
        self.assertEqual(load_task_state(self.root)["nextStep"], "resume exactly")

    def test_restore_refuses_divergent_local_content_without_force(self) -> None:
        self._init_git()
        (self.root / "tracked.txt").write_text("checkpoint version\n", encoding="utf-8")
        out = self.root / "saved.agent-checkpoint.zip"
        create_checkpoint(self.root, out=out)
        (self.root / "tracked.txt").write_text("someone else changed this\n", encoding="utf-8")
        with self.assertRaisesRegex(CheckpointError, "divergent local state"):
            restore_checkpoint(self.root, out)
        restore_checkpoint(self.root, out, force=True)
        self.assertEqual((self.root / "tracked.txt").read_text(), "checkpoint version\n")

    def test_restore_refuses_wrong_git_base_without_force(self) -> None:
        self._init_git()
        (self.root / "tracked.txt").write_text("checkpoint version\n", encoding="utf-8")
        out = self.root / "saved.agent-checkpoint.zip"
        create_checkpoint(self.root, out=out)
        self._git("add", "tracked.txt")
        self._git("commit", "-m", "move base")
        with self.assertRaisesRegex(CheckpointError, "does not match current HEAD"):
            restore_checkpoint(self.root, out)

    def test_checkpoint_manifest_detects_corruption(self) -> None:
        self._init_git()
        (self.root / "tracked.txt").write_text("checkpoint version\n", encoding="utf-8")
        out = self.root / "saved.agent-checkpoint.zip"
        create_checkpoint(self.root, out=out)
        broken = self.root / "broken.agent-checkpoint.zip"
        with zipfile.ZipFile(out, "r") as src, zipfile.ZipFile(broken, "w") as dst:
            for info in src.infolist():
                data = src.read(info.filename)
                if info.filename == "files/tracked.txt":
                    data += b"corrupt"
                dst.writestr(info, data)
        with self.assertRaisesRegex(CheckpointError, "hash mismatch"):
            inspect_checkpoint(broken)

    def test_no_git_released_source_manifest_allows_safe_restore_into_clean_base(self) -> None:
        original = b"base bytes\n"
        (self.root / "tracked.txt").write_bytes(original)
        import hashlib
        digest = hashlib.sha256(original).hexdigest()
        (self.root / "MANIFEST.sha256").write_text(
            "# agent-devtools-source-package-manifest-v1\n" + digest + "  tracked.txt\n",
            encoding="utf-8",
        )
        start_task(self.root, goal="Manifest recovery", next_step="continue")
        (self.root / "tracked.txt").write_bytes(b"unfinished bytes\n")
        update_task(self.root, changed_files=["tracked.txt"])
        out = self.root / "manifest.agent-checkpoint.zip"
        create_checkpoint(self.root, out=out)

        clean = self.root / "clean"
        clean.mkdir()
        (clean / "tracked.txt").write_bytes(original)
        (clean / "MANIFEST.sha256").write_text(
            "# agent-devtools-source-package-manifest-v1\n" + digest + "  tracked.txt\n",
            encoding="utf-8",
        )
        restore_checkpoint(clean, out)
        self.assertEqual((clean / "tracked.txt").read_bytes(), b"unfinished bytes\n")
        self.assertEqual(load_task_state(clean)["nextStep"], "continue")

    def test_no_git_resume_uses_task_changed_files_for_recovery(self) -> None:
        (self.root / "src").mkdir()
        (self.root / "src" / "work.py").write_text("value = 1\n", encoding="utf-8")
        start_task(self.root, goal="No Git resume", next_step="continue work")
        update_task(self.root, changed_files=["src/work.py"])
        brief = build_brief(self.root, mode="resume", include_context=False)
        self.assertEqual(brief["changeDiscovery"], "task")
        self.assertEqual(brief["changedFiles"], ["src/work.py"])
        self.assertIn("continue work", render_brief(brief))

    def test_no_git_checkpoint_uses_explicit_or_task_changed_files(self) -> None:
        (self.root / "src").mkdir()
        (self.root / "src" / "work.txt").write_text("unfinished\n", encoding="utf-8")
        start_task(self.root, goal="No Git", next_step="continue")
        update_task(self.root, changed_files=["src/work.txt"])
        out = self.root / "nogit.agent-checkpoint.zip"
        report = create_checkpoint(self.root, out=out)
        self.assertEqual(report["files"], 1)
        (self.root / "src" / "work.txt").unlink()
        task_state_path(self.root).unlink()
        restore_checkpoint(self.root, out)
        self.assertEqual((self.root / "src" / "work.txt").read_text(), "unfinished\n")
        self.assertEqual(load_task_state(self.root)["goal"], "No Git")

    def test_dotfile_paths_are_preserved(self) -> None:
        self._init_git()
        (self.root / ".env.example").write_text("A=2\n", encoding="utf-8")
        state = git_state(self.root)
        self.assertIn(".env.example", [row["path"] for row in state["changedFiles"]])


if __name__ == "__main__":
    unittest.main()
