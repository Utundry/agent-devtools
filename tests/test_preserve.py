from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from agent_devtools.preserve import PreserveError, _kind_from_archive, create_preservation, restore_preservation


class PreserveTests(unittest.TestCase):
    def make_root(self, profile: str):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": profile}}),
            encoding="utf-8",
        )
        return tmp, root

    def test_development_create_routes_to_checkpoint(self) -> None:
        tmp, root = self.make_root("development")
        try:
            with patch(
                "agent_devtools.preserve.create_checkpoint",
                return_value={"status": "pass", "path": "/tmp/x.zip", "sha256": "a" * 64, "files": 2},
            ) as create:
                payload = create_preservation(root, include=["src/a.py"], max_bytes=123)
            create.assert_called_once_with(root.resolve(), out=None, include=["src/a.py"], max_bytes=123)
            self.assertEqual("checkpoint", payload["kind"])
        finally:
            tmp.cleanup()

    def test_non_development_create_routes_to_workspace_snapshot(self) -> None:
        tmp, root = self.make_root("research")
        try:
            with patch(
                "agent_devtools.preserve.create_snapshot",
                return_value={"status": "pass", "path": "/tmp/x.zip", "sha256": "b" * 64, "artifacts": 4},
            ) as create:
                payload = create_preservation(root, max_bytes=456)
            create.assert_called_once_with(root.resolve(), out=None, max_bytes=456)
            self.assertEqual("workspace-snapshot", payload["kind"])
        finally:
            tmp.cleanup()

    def test_non_development_include_fails_instead_of_being_silently_ignored(self) -> None:
        tmp, root = self.make_root("analysis")
        try:
            with self.assertRaisesRegex(PreserveError, "development-checkpoint specific"):
                create_preservation(root, include=["notes.md"])
        finally:
            tmp.cleanup()

    def test_non_development_snapshot_excludes_default_sensitive_and_project_patterns(self) -> None:
        tmp, root = self.make_root("research")
        try:
            config = json.loads((root / "agent-tools.json").read_text(encoding="utf-8"))
            config["preserve"] = {"exclude": ["private/**"]}
            (root / "agent-tools.json").write_text(json.dumps(config), encoding="utf-8")
            (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
            (root / "credentials.json").write_text('{"secret":true}\n', encoding="utf-8")
            (root / "id_ed25519").write_text("private-key\n", encoding="utf-8")
            (root / "nested").mkdir()
            (root / "nested/.env.local").write_text("PASSWORD=secret\n", encoding="utf-8")
            (root / "private").mkdir()
            (root / "private/notes.md").write_text("private\n", encoding="utf-8")
            (root / "notes.md").write_text("keep\n", encoding="utf-8")

            out = root / "snapshot.zip"
            payload = create_preservation(root, out=out)
            self.assertEqual("workspace-snapshot", payload["kind"])
            with zipfile.ZipFile(out, "r") as zf:
                names = set(zf.namelist())
                meta = json.loads(zf.read("snapshot.json").decode("utf-8"))
            self.assertIn("artifacts/notes.md", names)
            self.assertNotIn("artifacts/.env", names)
            self.assertNotIn("artifacts/credentials.json", names)
            self.assertNotIn("artifacts/id_ed25519", names)
            self.assertNotIn("artifacts/nested/.env.local", names)
            self.assertNotIn("artifacts/private/notes.md", names)
            self.assertTrue(meta["sensitiveDefaultsExcluded"])
            self.assertEqual(["private/**"], meta["projectPreserveExclude"])
        finally:
            tmp.cleanup()

    def test_non_development_snapshot_rejects_invalid_preserve_exclude_contract(self) -> None:
        tmp, root = self.make_root("analysis")
        try:
            config = json.loads((root / "agent-tools.json").read_text(encoding="utf-8"))
            config["preserve"] = {"exclude": "../outside"}
            (root / "agent-tools.json").write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(PreserveError, "preserve.exclude must be an array"):
                create_preservation(root)
        finally:
            tmp.cleanup()

    def test_kind_detection_is_archive_semantic_not_filename_based(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            checkpoint = root / "anything.zip"
            snapshot = root / "other.bin"
            with zipfile.ZipFile(checkpoint, "w") as zf:
                zf.writestr("checkpoint.json", "{}")
            with zipfile.ZipFile(snapshot, "w") as zf:
                zf.writestr("snapshot.json", "{}")
            self.assertEqual("checkpoint", _kind_from_archive(checkpoint))
            self.assertEqual("workspace-snapshot", _kind_from_archive(snapshot))

    def test_restore_auto_routes_from_archive_kind(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "preserved.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("checkpoint.json", "{}")
            target = root / "target"
            with patch(
                "agent_devtools.preserve.restore_checkpoint",
                return_value={"status": "pass", "restoredFiles": 1},
            ) as restore:
                payload = restore_preservation(root, archive, target=target, force=True)
            restore.assert_called_once_with(target.resolve(), archive, force=True)
            self.assertEqual("checkpoint", payload["kind"])


if __name__ == "__main__":
    unittest.main()
