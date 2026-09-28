from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_devtools import __version__
from agent_devtools.self_update import SelfUpdateError, extract_pinned_release, self_update


class SelfUpdateTests(unittest.TestCase):
    def test_extract_pinned_release_requires_one_unambiguous_release(self) -> None:
        text = (
            "https://raw.githubusercontent.com/Utundry/agent-devtools/"
            "v0.8.4/bootstrap/AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py"
        )
        version, url = extract_pinned_release(text)
        self.assertEqual("0.8.4", version)
        self.assertIn("/v0.8.4/bootstrap/", url)

        with self.assertRaisesRegex(SelfUpdateError, "exactly one"):
            extract_pinned_release(text + "\n" + text.replace("0.8.4", "0.8.5"))

    def test_check_only_explicit_version_needs_no_network(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            payload = self_update(
                Path(td),
                requested_version="9.9.9",
                check_only=True,
                fetch=lambda *_args: (_ for _ in ()).throw(AssertionError("network used")),
            )
            self.assertEqual("update-available", payload["status"])
            self.assertEqual("9.9.9", payload["targetVersion"])
            self.assertFalse(payload["performed"])

    def test_update_proves_installer_and_installed_runtime_identity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = "9.9.9"

            def fetch(url: str, timeout: float) -> bytes:
                self.assertGreater(timeout, 0)
                if url.endswith("AGENT-START-HERE.md"):
                    return (
                        "https://raw.githubusercontent.com/Utundry/agent-devtools/"
                        f"v{target}/bootstrap/AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py"
                    ).encode()
                return b"#!/usr/bin/env python3\n# synthetic installer\n"

            def completed(payload: dict) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(
                    args=["synthetic"],
                    returncode=0,
                    stdout=json.dumps(payload),
                    stderr="",
                )

            def runner(argv):
                if "--self-check" in argv:
                    return completed({
                        "status": "pass",
                        "releaseVersion": target,
                        "embeddedKit": f"agent-devtools-integration-update-{target}-minimal.zip",
                        "embeddedKitSha256": "a" * 64,
                    })
                if "--target" in argv:
                    installed = root / "devtools" / "agent" / "agent_devtools"
                    installed.mkdir(parents=True)
                    (installed / "__init__.py").write_text(
                        f'__version__ = "{target}"\n',
                        encoding="utf-8",
                    )
                    (root / "devtools" / "agent" / "agent.py").write_text(
                        "# synthetic\n",
                        encoding="utf-8",
                    )
                    return completed({"status": "ready", "mode": "update-existing"})
                if "capabilities" in argv:
                    return completed({"toolVersion": target})
                raise AssertionError(argv)

            payload = self_update(root, fetch=fetch, runner=runner)
            self.assertEqual("updated", payload["status"])
            self.assertEqual(target, payload["installedVersion"])
            self.assertEqual(target, payload["runtime"]["installedToolVersion"])

    def test_current_explicit_version_is_frictionless_noop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            payload = self_update(
                Path(td),
                requested_version=__version__,
                fetch=lambda *_args: (_ for _ in ()).throw(AssertionError("network used")),
            )
            self.assertEqual("up-to-date", payload["status"])
            self.assertFalse(payload["performed"])


if __name__ == "__main__":
    unittest.main()
