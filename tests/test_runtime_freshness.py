from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_devtools import cli
from agent_devtools.workflow import capabilities
from agent_devtools.presets import list_presets
from agent_devtools.self_update import (
    SelfUpdateError,
    load_runtime_update_policy,
    periodic_runtime_freshness,
    redispatch_to_installed_runtime,
)


class RuntimeFreshnessTests(unittest.TestCase):
    def make_root(self, runtime_update=None):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        payload = {"version": 1, "work": {"profile": "general"}}
        if runtime_update is not None:
            payload["runtimeUpdate"] = runtime_update
        (root / "agent-tools.json").write_text(json.dumps(payload), encoding="utf-8")
        return tmp, root

    def test_missing_policy_is_off_and_never_checks_network(self):
        tmp, root = self.make_root()
        try:
            with patch("agent_devtools.self_update.self_update") as update:
                payload = periodic_runtime_freshness(root, now=1000)
            update.assert_not_called()
            self.assertEqual("disabled", payload["status"])
            self.assertFalse(payload["checked"])
        finally:
            tmp.cleanup()

    def test_policy_supports_off_notify_auto_and_invalid_fails_soft(self):
        for mode in ("off", "notify", "auto"):
            tmp, root = self.make_root({"mode": mode, "checkIntervalHours": 12})
            try:
                policy = load_runtime_update_policy(root)
                self.assertEqual(mode, policy["mode"])
                self.assertEqual(12.0, policy["checkIntervalHours"])
            finally:
                tmp.cleanup()
        tmp, root = self.make_root({"mode": "surprise", "checkIntervalHours": "nan"})
        try:
            policy = load_runtime_update_policy(root)
            self.assertEqual("off", policy["mode"])
            self.assertEqual(24.0, policy["checkIntervalHours"])
        finally:
            tmp.cleanup()

    def test_notify_checks_once_per_ttl_and_then_uses_cache(self):
        tmp, root = self.make_root({"mode": "notify", "checkIntervalHours": 24})
        try:
            result = {
                "status": "update-available",
                "currentVersion": "0.16.35",
                "targetVersion": "0.16.36",
                "updateAvailable": True,
                "performed": False,
            }
            with patch("agent_devtools.self_update.self_update", return_value=result) as update:
                first = periodic_runtime_freshness(root, now=1000)
                second = periodic_runtime_freshness(root, now=1001)
            update.assert_called_once()
            self.assertTrue(update.call_args.kwargs["check_only"])
            self.assertEqual("update-available", first["status"])
            self.assertEqual("cached", second["status"])
            self.assertEqual("update-available", second["cachedStatus"])
        finally:
            tmp.cleanup()

    def test_auto_performs_update_when_due(self):
        tmp, root = self.make_root({"mode": "auto", "checkIntervalHours": 24})
        try:
            result = {
                "status": "updated",
                "currentVersion": "0.16.35",
                "targetVersion": "0.16.36",
                "updateAvailable": False,
                "performed": True,
                "installedVersion": "0.16.36",
            }
            with patch("agent_devtools.self_update.self_update", return_value=result) as update:
                payload = periodic_runtime_freshness(root, now=2000)
            update.assert_called_once()
            self.assertFalse(update.call_args.kwargs["check_only"])
            self.assertEqual("updated", payload["status"])
            self.assertEqual("0.16.36", payload["installedVersion"])
        finally:
            tmp.cleanup()

    def test_cache_write_failure_never_blocks_routine_work(self):
        tmp, root = self.make_root({"mode": "notify", "checkIntervalHours": 24})
        try:
            result = {
                "status": "up-to-date",
                "currentVersion": "0.16.35",
                "targetVersion": "0.16.35",
                "updateAvailable": False,
                "performed": False,
            }
            with patch("agent_devtools.self_update.self_update", return_value=result), \
                 patch("agent_devtools.self_update.atomic_json_write", side_effect=OSError("read-only")):
                payload = periodic_runtime_freshness(root, now=2500)
            self.assertEqual("up-to-date", payload["status"])
            self.assertFalse(payload["stateRecorded"])
            self.assertTrue(payload["nonBlocking"])
        finally:
            tmp.cleanup()

    def test_network_failure_is_warning_and_is_ttl_cached(self):
        tmp, root = self.make_root({"mode": "notify", "checkIntervalHours": 24})
        try:
            with patch("agent_devtools.self_update.self_update", side_effect=SelfUpdateError("offline")) as update:
                first = periodic_runtime_freshness(root, now=3000)
                second = periodic_runtime_freshness(root, now=3001)
            update.assert_called_once()
            self.assertEqual("warn", first["status"])
            self.assertTrue(first["nonBlocking"])
            self.assertEqual("cached", second["status"])
            self.assertEqual("warn", second["cachedStatus"])
        finally:
            tmp.cleanup()

    def test_begin_notice_uses_stderr_without_corrupting_json_stdout(self):
        tmp, root = self.make_root({"mode": "notify", "checkIntervalHours": 24})
        try:
            stdout = io.StringIO()
            stderr = io.StringIO()
            freshness = {
                "status": "update-available",
                "currentVersion": "0.16.35",
                "targetVersion": "0.16.36",
            }
            def fake_begin(_root, _args):
                print(json.dumps({"status": "ok"}))
                return 0
            with patch("agent_devtools.cli.discover_project_root", return_value=root), \
                 patch("agent_devtools.cli.periodic_runtime_freshness", return_value=freshness), \
                 patch("agent_devtools.cli.work_cli.main_begin", side_effect=fake_begin), \
                 contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                rc = cli._main_impl(["begin", "--goal", "x", "--json"])
            self.assertEqual(0, rc)
            self.assertEqual({"status": "ok"}, json.loads(stdout.getvalue()))
            self.assertIn("runtime update available", stderr.getvalue())
        finally:
            tmp.cleanup()

    def test_redispatch_uses_fresh_agent_and_inherited_standard_streams(self):
        tmp, root = self.make_root({"mode": "auto", "checkIntervalHours": 24})
        try:
            agent = root / "devtools" / "agent" / "agent.py"
            agent.parent.mkdir(parents=True)
            agent.write_text("# fresh runtime\n", encoding="utf-8")
            seen = {}

            def runner(argv, **kwargs):
                seen["argv"] = list(argv)
                seen["kwargs"] = dict(kwargs)
                return type("P", (), {"returncode": 7})()

            rc = redispatch_to_installed_runtime(
                root,
                ["begin", "--goal", "continue", "--json"],
                runner=runner,
            )
            self.assertEqual(7, rc)
            self.assertEqual(str(agent), seen["argv"][1])
            self.assertEqual(["begin", "--goal", "continue", "--json"], seen["argv"][2:])
            self.assertEqual(root, seen["kwargs"]["cwd"])
            self.assertEqual("1", seen["kwargs"]["env"]["AGENT_DEVTOOLS_RUNTIME_REENTRY"])
            self.assertNotIn("stdout", seen["kwargs"])
            self.assertNotIn("stderr", seen["kwargs"])
        finally:
            tmp.cleanup()

    def test_begin_auto_update_hands_original_command_to_fresh_runtime(self):
        tmp, root = self.make_root({"mode": "auto", "checkIntervalHours": 24})
        try:
            freshness = {
                "status": "updated",
                "currentVersion": "0.16.35",
                "targetVersion": "0.16.36",
                "installedVersion": "0.16.36",
            }
            with patch("agent_devtools.cli.discover_project_root", return_value=root), \
                 patch("agent_devtools.cli.periodic_runtime_freshness", return_value=freshness), \
                 patch("agent_devtools.cli.redispatch_to_installed_runtime", return_value=0) as redispatch, \
                 patch("agent_devtools.cli.work_cli.main_begin") as old_begin:
                rc = cli._main_impl(["begin", "--goal", "continue", "--json"])
            self.assertEqual(0, rc)
            redispatch.assert_called_once_with(root, ["begin", "--goal", "continue", "--json"])
            old_begin.assert_not_called()
        finally:
            tmp.cleanup()

    def test_doctor_auto_update_hands_command_to_fresh_runtime(self):
        tmp, root = self.make_root({"mode": "auto", "checkIntervalHours": 24})
        try:
            freshness = {
                "status": "updated",
                "currentVersion": "0.16.35",
                "targetVersion": "0.16.36",
                "installedVersion": "0.16.36",
            }
            with patch("agent_devtools.cli.discover_project_root", return_value=root), \
                 patch("agent_devtools.cli.periodic_runtime_freshness", return_value=freshness), \
                 patch("agent_devtools.cli.redispatch_to_installed_runtime", return_value=0) as redispatch, \
                 patch("agent_devtools.cli._doctor") as old_doctor:
                rc = cli._main_impl(["doctor", "--json"])
            self.assertEqual(0, rc)
            redispatch.assert_called_once_with(root, ["doctor", "--json"])
            old_doctor.assert_not_called()
        finally:
            tmp.cleanup()

    def test_reentry_failure_is_warning_and_old_begin_still_runs(self):
        tmp, root = self.make_root({"mode": "auto", "checkIntervalHours": 24})
        try:
            freshness = {
                "status": "updated",
                "currentVersion": "0.16.35",
                "targetVersion": "0.16.36",
                "installedVersion": "0.16.36",
            }
            stderr = io.StringIO()
            with patch("agent_devtools.cli.discover_project_root", return_value=root), \
                 patch("agent_devtools.cli.periodic_runtime_freshness", return_value=freshness), \
                 patch(
                     "agent_devtools.cli.redispatch_to_installed_runtime",
                     side_effect=SelfUpdateError("synthetic handoff failure"),
                 ), \
                 patch("agent_devtools.cli.work_cli.main_begin", return_value=0) as old_begin, \
                 contextlib.redirect_stderr(stderr):
                rc = cli._main_impl(["begin", "--goal", "continue"])
            self.assertEqual(0, rc)
            old_begin.assert_called_once()
            self.assertIn("runtime re-entry warning", stderr.getvalue())
        finally:
            tmp.cleanup()

    def test_all_bundled_presets_default_new_workspaces_to_notify(self):
        for preset in list_presets():
            self.assertEqual(
                {"mode": "notify", "checkIntervalHours": 24},
                preset.files["agent-tools.json"]["runtimeUpdate"],
                preset.preset_id,
            )

    def test_capabilities_advertise_periodic_freshness_without_daemon(self):
        tmp, root = self.make_root()
        try:
            payload = capabilities(root)["commands"]["selfUpdate"]
            self.assertTrue(payload["periodicFreshness"])
            self.assertEqual(["off", "notify", "auto"], payload["modes"])
            self.assertTrue(payload["ttlCache"])
            self.assertTrue(payload["freshRuntimeCommandHandoff"])
            self.assertTrue(payload["nonBlockingReentryFallback"])
            self.assertFalse(payload["daemon"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
