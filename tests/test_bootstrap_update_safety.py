from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_devtools.self_update import periodic_runtime_freshness


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "bootstrap_agent_devtools_update_safety",
    ROOT / "bootstrap" / "BOOTSTRAP_AGENT_DEVTOOLS.py",
)
assert SPEC and SPEC.loader
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class BootstrapUpdateSafetyTests(unittest.TestCase):
    def test_bootstrap_post_check_runner_avoids_subprocess_pipes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)

            def fake_run(argv, **kwargs):
                self.assertIs(kwargs.get("stdin"), subprocess.DEVNULL)
                self.assertIsNot(kwargs.get("stdout"), subprocess.PIPE)
                self.assertIsNot(kwargs.get("stderr"), subprocess.PIPE)
                self.assertNotIn("capture_output", kwargs)
                self.assertNotIn("text", kwargs)
                kwargs["stdout"].write("готово\n".encode("utf-8"))
                kwargs["stderr"].write("диагностика\n".encode("utf-8"))
                return subprocess.CompletedProcess(args=list(argv), returncode=0)

            with patch.object(bootstrap.subprocess, "run", side_effect=fake_run):
                payload = bootstrap._run(["synthetic", "post-check"], cwd)

            self.assertTrue(payload["ok"])
            self.assertEqual("готово\n", payload["stdout"])
            self.assertEqual("диагностика\n", payload["stderr"])

    def test_bootstrap_post_checks_suppress_periodic_runtime_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            calls = []

            def fake_run(cmd, cwd, *, env=None):
                calls.append((list(cmd), cwd, dict(env or {})))
                return {"argv": cmd, "exitCode": 0, "stdout": "{}", "stderr": "", "ok": True}

            with patch.object(bootstrap, "_run", side_effect=fake_run):
                checks = bootstrap._post_checks(target)

            self.assertEqual(2, len(checks))
            self.assertEqual(2, len(calls))
            for _cmd, cwd, env in calls:
                self.assertEqual(target, cwd)
                self.assertEqual("1", env["AGENT_DEVTOOLS_DISABLE_RUNTIME_FRESHNESS"])

    def test_internal_suppression_never_checks_network(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "agent-tools.json").write_text(
                '{"version":1,"runtimeUpdate":{"mode":"auto","checkIntervalHours":24}}',
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {"AGENT_DEVTOOLS_DISABLE_RUNTIME_FRESHNESS": "1"},
                clear=False,
            ), patch("agent_devtools.self_update.self_update") as update:
                payload = periodic_runtime_freshness(root, now=1000)

            update.assert_not_called()
            self.assertEqual("suppressed", payload["status"])
            self.assertFalse(payload["checked"])
            self.assertEqual("internal-bootstrap-post-check", payload["reason"])


if __name__ == "__main__":
    unittest.main()
