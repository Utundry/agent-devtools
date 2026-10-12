from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_devtools import __version__
from agent_devtools.bootstrap import BootstrapError, refresh_existing_runtime


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "agent_devtools_update_release_runtime_refresh",
    PROJECT_ROOT / "scripts" / "update_release.py",
)
assert SPEC is not None and SPEC.loader is not None
updater = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = updater
SPEC.loader.exec_module(updater)


class ExistingRuntimeRefreshTests(unittest.TestCase):
    def _stale_runtime(self, root: Path) -> Path:
        vendor = root / "devtools" / "agent"
        package = vendor / "agent_devtools"
        package.mkdir(parents=True)
        (vendor / "agent.py").write_text("# stale\n", encoding="utf-8")
        (package / "__init__.py").write_text('__version__ = "0.0.0"\n', encoding="utf-8")
        return vendor

    def test_payload_refresh_replaces_stale_runtime_without_project_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            vendor = self._stale_runtime(root)

            result = refresh_existing_runtime(root, source_root=PROJECT_ROOT)

            self.assertEqual("pass", result["status"])
            self.assertEqual("replace", result["action"])
            self.assertEqual(__version__, result["installedVersion"])
            self.assertFalse((root / "agent-tools.json").exists())
            self.assertFalse((root / "AGENTS.md").exists())
            self.assertIn(
                f'__version__ = "{__version__}"',
                (vendor / "agent_devtools" / "__init__.py").read_text(encoding="utf-8"),
            )

    def test_payload_refresh_skips_when_runtime_is_not_installed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = refresh_existing_runtime(root, source_root=PROJECT_ROOT)
            self.assertEqual("skipped", result["status"])
            self.assertEqual("runtime-not-installed", result["reason"])
            self.assertFalse((root / "devtools").exists())

    def test_payload_refresh_refuses_legacy_config_inside_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            vendor = self._stale_runtime(root)
            (vendor / "agent-tools.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaises(BootstrapError):
                refresh_existing_runtime(root, source_root=PROJECT_ROOT)

    def test_updater_refresh_is_nonfatal_after_publish(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._stale_runtime(root)
            with patch.object(
                updater,
                "refresh_existing_runtime",
                side_effect=BootstrapError("synthetic refresh failure"),
            ):
                result = updater._refresh_existing_local_runtime(root)
            self.assertEqual("warn", result["status"])
            self.assertIn("synthetic refresh failure", result["error"])

    def test_updater_source_contains_post_publish_refresh_hook_and_report(self) -> None:
        source = (PROJECT_ROOT / "scripts" / "update_release.py").read_text(encoding="utf-8")
        self.assertIn(
            "local_runtime_refresh = _refresh_existing_local_runtime(root)",
            source,
        )
        self.assertIn('"localRuntimeRefresh": local_runtime_refresh', source)


if __name__ == "__main__":
    unittest.main()
