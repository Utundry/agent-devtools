from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "update_release_process_safety",
    ROOT / "scripts" / "update_release.py",
)
assert SPEC is not None and SPEC.loader is not None
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)


class _CompletedProcess:
    pid = 12345

    def __init__(self, argv, kwargs):
        self.args = list(argv)
        self.returncode = 0
        kwargs["stdout"].write(b"captured stdout\n")
        kwargs["stderr"].write(b"captured stderr\n")

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode


class _TimeoutProcess:
    pid = 54321

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired(["synthetic"], timeout)

    def poll(self):
        return None


class UpdateReleaseProcessSafetyTests(unittest.TestCase):
    def test_captured_runner_uses_files_not_subprocess_pipes(self) -> None:
        observed = {}

        def fake_popen(argv, **kwargs):
            observed.update(kwargs)
            return _CompletedProcess(argv, kwargs)

        with mock.patch.object(updater.subprocess, "Popen", side_effect=fake_popen):
            result = updater._run_captured(
                ["synthetic", "child"],
                hard_timeout=1.0,
                label="synthetic child",
            )

        self.assertIs(observed["stdin"], subprocess.DEVNULL)
        self.assertIsNot(observed["stdout"], subprocess.PIPE)
        self.assertIsNot(observed["stderr"], subprocess.PIPE)
        self.assertTrue(observed["close_fds"])
        self.assertEqual("captured stdout\n", result.stdout)
        self.assertEqual("captured stderr\n", result.stderr)

    def test_watchdog_terminates_process_tree_before_failing(self) -> None:
        proc = _TimeoutProcess()
        with mock.patch.object(updater, "_terminate_process_tree") as terminate:
            with self.assertRaisesRegex(updater.UpdateError, "hard timeout"):
                updater._wait_with_watchdog(
                    proc,
                    label="synthetic child",
                    hard_timeout=0.01,
                )
        terminate.assert_called_once_with(proc)

    def test_git_uses_hardened_captured_runner(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout="ok\n",
            stderr="",
        )
        with mock.patch.object(updater, "_run_captured", return_value=completed) as run:
            result = updater.git(ROOT, "status", "--porcelain")
        self.assertEqual("ok\n", result.stdout)
        kwargs = run.call_args.kwargs
        self.assertEqual(updater._GIT_HARD_TIMEOUT_SECONDS, kwargs["hard_timeout"])
        self.assertIn("git status --porcelain", kwargs["label"])


if __name__ == "__main__":
    unittest.main()
