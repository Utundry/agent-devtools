from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_release.py"
SPEC = importlib.util.spec_from_file_location("update_release_test", SCRIPT)
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)

# The fixture publisher uses real commits, tags, atomic push and a local bare
# remote. Expensive qualification belongs to the existing canonical builder;
# here we test isolation, worktree selection, preservation and orchestration.
PUBLISHER = '''import argparse, pathlib, subprocess, sys
p = argparse.ArgumentParser()
p.add_argument('--version', required=True)
p.add_argument('--publish', action='store_true')
p.add_argument('--remote', default='origin')
a = p.parse_args()
r = pathlib.Path.cwd()
assert not subprocess.check_output(['git', 'status', '--porcelain']).strip()
if a.version.endswith('-fail'):
    sys.exit(7)
(r / 'VERSION').write_text(a.version + '\\n')
(r / 'bootstrap' / 'AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py').write_text(a.version)
(r / 'bootstrap' / 'agent-devtools-bootstrap-kit.zip').write_bytes(a.version.encode())
if a.publish:
    subprocess.run(['git', 'add', '-A'], check=True)
    subprocess.run(['git', 'commit', '-qm', 'Release ' + a.version], check=True)
    subprocess.run(['git', 'tag', '-a', 'v' + a.version, '-m', a.version], check=True)
    subprocess.run(['git', 'push', '--atomic', a.remote, 'HEAD:refs/heads/main',
                    'refs/tags/v' + a.version], check=True)
'''


@unittest.skipUnless(shutil.which("git"), "Git is required")
class UpdateReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "source with spaces"
        self.remote = self.base / "remote.git"
        self.root.mkdir()
        self.run_git(self.root, "init", "-q", "-b", "main")
        self.run_git(self.root, "config", "user.name", "Fixture")
        self.run_git(self.root, "config", "user.email", "fixture@example.invalid")
        (self.root / "scripts").mkdir()
        (self.root / "scripts" / "build_release.py").write_text(PUBLISHER)
        (self.root / "bootstrap").mkdir()
        for name in ("AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py", "agent-devtools-bootstrap-kit.zip"):
            (self.root / "bootstrap" / name).write_text("before")
        (self.root / "VERSION").write_text("1.0.0\n")
        (self.root / "source.txt").write_text("before\n")
        (self.root / "local.txt").write_text("tracked baseline\n")
        (self.root / ".gitignore").write_text("build/\n")
        self.run_git(self.root, "add", "-A")
        self.run_git(self.root, "commit", "-qm", "base")
        self.initial = self.run_git(self.root, "rev-parse", "HEAD").strip()
        self.run_git(self.root, "init", "--bare", "-q", str(self.remote))
        self.run_git(self.root, "remote", "add", "origin", str(self.remote))
        self.run_git(self.root, "push", "-q", "-u", "origin", "main")

    @staticmethod
    def run_git(root: Path, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(root), *args],
                                capture_output=True, text=True, check=True)
        return result.stdout

    def args(self, **kwargs) -> argparse.Namespace:
        return argparse.Namespace(**dict(
            repo=str(self.root), branch="main", remote="origin", version="1.0.1",
            patch=None, publish=True, **kwargs,
        ))

    def update(self, **kwargs) -> dict:
        values = vars(self.args()).copy()
        values.update(kwargs)
        with contextlib.redirect_stdout(io.StringIO()):
            return updater.update(argparse.Namespace(**values))

    def patch(self) -> Path:
        (self.root / "source.txt").write_text("after\n")
        (self.root / "new.txt").write_text("new upstream file\n")
        self.run_git(self.root, "add", "source.txt", "new.txt")
        patch = self.base / "update.patch"
        patch.write_text(self.run_git(self.root, "diff", "--binary", "HEAD"))
        self.run_git(self.root, "reset", "--hard", "HEAD")
        return patch

    def test_another_worktree_routes_to_main_and_preserves_unrelated_local_files(self) -> None:
        other = self.base / "audit worktree"
        self.run_git(self.root, "worktree", "add", "-b", "audit", str(other))
        (self.root / "local.txt").write_text("my local edit\n")
        (self.root / "devtools").mkdir()
        (self.root / "devtools" / "runtime.py").write_text("installed runtime\n")
        (self.root / ".agent-bootstrap-report.json").write_text("{}\n")
        report = self.update(repo=str(other))
        self.assertEqual("published", report["status"])
        self.assertEqual(str(self.root), report["originalWorktree"])
        self.assertIsNone(report["localBackupStash"])
        self.assertEqual("my local edit\n", (self.root / "local.txt").read_text())
        self.assertEqual("installed runtime\n", (self.root / "devtools" / "runtime.py").read_text())
        self.assertEqual("{}\n", (self.root / ".agent-bootstrap-report.json").read_text())
        self.assertEqual(self.initial, self.run_git(other, "rev-parse", "HEAD").strip())
        self.assertEqual("1.0.1\n", (self.root / "VERSION").read_text())
        self.assertEqual(report["head"], self.run_git(self.remote, "rev-parse", "main").strip())
        self.assertTrue((Path(report["artifacts"]) / "build-release.log").is_file())

    def test_overlapping_old_edits_and_untracked_file_are_backed_up_without_losing_old_stash(self) -> None:
        patch = self.patch()
        (self.root / "local.txt").write_text("previous stash content\n")
        self.run_git(self.root, "stash", "push", "-m", "old backup")
        old = self.run_git(self.root, "rev-parse", "refs/stash").strip()
        (self.root / "source.txt").write_text("pre-existing partial fix\n")
        (self.root / "new.txt").write_text("my untracked file\n")
        report = self.update(patch=str(patch))
        backup = report["localBackupStash"]
        self.assertIsNotNone(backup)
        self.assertEqual("after\n", (self.root / "source.txt").read_text())
        self.assertEqual("new upstream file\n", (self.root / "new.txt").read_text())
        self.assertEqual("pre-existing partial fix\n", self.run_git(self.root, "show", f"{backup}:source.txt"))
        self.assertEqual("my untracked file\n", self.run_git(self.root, "show", f"{backup}^3:new.txt"))
        self.assertIn(old, self.run_git(self.root, "stash", "list", "--format=%H"))

    def test_failed_qualification_never_changes_original_or_remote(self) -> None:
        (self.root / "source.txt").write_text("local edit\n")
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(updater.UpdateError, "builder failed"):
                self.update(version="1.0.1-fail")
        self.assertEqual(self.initial, self.run_git(self.root, "rev-parse", "HEAD").strip())
        self.assertEqual(self.initial, self.run_git(self.remote, "rev-parse", "main").strip())
        self.assertEqual("local edit\n", (self.root / "source.txt").read_text())
        self.assertEqual("", self.run_git(self.root, "stash", "list"))

    def test_retry_is_idempotent_and_skips_builder(self) -> None:
        first = self.update()
        second = self.update()
        self.assertEqual("already-published", second["status"])
        self.assertEqual(first["head"], second["head"])
        self.assertFalse((Path(second["artifacts"]) / "build-release.log").exists())

    def test_same_patch_retry_survives_publisher_version_changes(self) -> None:
        (self.root / "source.txt").write_text("patched\n")
        (self.root / "VERSION").write_text("1.0.1-rc.1\n")
        patch = self.base / "versioned.patch"
        patch.write_text(self.run_git(self.root, "diff", "--binary", "HEAD"))
        self.run_git(self.root, "restore", "source.txt", "VERSION")
        first = self.update(patch=str(patch))
        second = self.update(patch=str(patch))
        self.assertEqual("already-published", second["status"])
        self.assertEqual(first["head"], second["head"])
        self.assertEqual("1.0.1\n", (self.root / "VERSION").read_text())
        patch.write_text(patch.read_text().replace("+patched", "+another patch"))
        with self.assertRaisesRegex(updater.UpdateError, "Cannot verify"):
            self.update(patch=str(patch))

    def test_incompatible_patch_leaves_original_untouched(self) -> None:
        patch = self.patch()
        (self.root / "source.txt").write_text("different committed base\n")
        self.run_git(self.root, "add", "source.txt")
        self.run_git(self.root, "commit", "-qm", "different source")
        head = self.run_git(self.root, "rev-parse", "HEAD").strip()
        with self.assertRaisesRegex(updater.UpdateError, "does not match"):
            self.update(patch=str(patch))
        self.assertEqual(head, self.run_git(self.root, "rev-parse", "HEAD").strip())
        self.assertEqual(self.initial, self.run_git(self.remote, "rev-parse", "main").strip())

    def test_prepare_only_saves_artifacts_and_does_not_publish(self) -> None:
        report = self.update(publish=False)
        self.assertEqual("prepared", report["status"])
        self.assertEqual(self.initial, self.run_git(self.remote, "rev-parse", "main").strip())
        self.assertEqual(self.initial, self.run_git(self.root, "rev-parse", "HEAD").strip())
        artifacts = Path(report["artifacts"])
        self.assertEqual("1.0.1", (artifacts / "AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py").read_text())
        self.assertEqual(report, json.loads((artifacts / "update-result.json").read_text()))

    def test_diverged_remote_fails_without_touching_local_changes(self) -> None:
        other = self.base / "remote writer"
        self.run_git(self.root, "clone", "-q", "--branch", "main", str(self.remote), str(other))
        self.run_git(other, "config", "user.name", "Writer")
        self.run_git(other, "config", "user.email", "writer@example.invalid")
        (other / "source.txt").write_text("remote change\n")
        self.run_git(other, "add", "source.txt")
        self.run_git(other, "commit", "-qm", "remote")
        self.run_git(other, "push", "-q", "origin", "main")
        (self.root / "source.txt").write_text("local commit\n")
        self.run_git(self.root, "add", "source.txt")
        self.run_git(self.root, "commit", "-qm", "local")
        local = self.run_git(self.root, "rev-parse", "HEAD").strip()
        (self.root / "local.txt").write_text("unsaved\n")
        with self.assertRaisesRegex(updater.UpdateError, "diverged"):
            self.update()
        self.assertEqual(local, self.run_git(self.root, "rev-parse", "HEAD").strip())
        self.assertEqual("unsaved\n", (self.root / "local.txt").read_text())


if __name__ == "__main__":
    unittest.main()
