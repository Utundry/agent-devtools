from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from agent_devtools.handoff import HandoffError, create_handoff, inspect_handoff, resume_handoff


class HandoffTests(unittest.TestCase):
    def make_root(self, profile: str):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "agent-tools.json").write_text(
            json.dumps({"version": 1, "work": {"profile": profile}}),
            encoding="utf-8",
        )
        return tmp, root

    @staticmethod
    def fake_preservation(kind: str):
        def create(_root, out, include, max_bytes):
            with zipfile.ZipFile(out, "w") as zf:
                zf.writestr(
                    "checkpoint.json" if kind == "checkpoint" else "snapshot.json",
                    "{}",
                )
            return {"kind": kind, "artifact": {"status": "pass"}}
        return create

    def test_create_packages_brief_and_profile_aware_preservation(self) -> None:
        tmp, root = self.make_root("development")
        try:
            out = root / "handoff.zip"
            brief = {
                "format": "agent-devtools-work-brief",
                "profile": {"id": "development"},
                "workingStateFingerprint": "fingerprint",
                "recovery": {"recommendedAction": "continue"},
            }
            with patch(
                "agent_devtools.handoff.create_preservation",
                side_effect=self.fake_preservation("checkpoint"),
            ), patch(
                "agent_devtools.handoff.build_brief",
                return_value=brief,
            ), patch(
                "agent_devtools.handoff.inspect_preservation",
                return_value={"kind": "checkpoint", "artifact": {"status": "pass"}},
            ):
                payload = create_handoff(root, out=out, include=["src/a.py"])
                inspected = inspect_handoff(out)
            self.assertEqual("checkpoint", payload["kind"])
            self.assertEqual("fingerprint", inspected["workingStateFingerprint"])
        finally:
            tmp.cleanup()

    def test_create_rejects_tiny_brief_budget(self) -> None:
        tmp, root = self.make_root("general")
        try:
            with self.assertRaisesRegex(HandoffError, "budget"):
                create_handoff(root, budget=64)
        finally:
            tmp.cleanup()

    def test_resume_restores_preservation_then_builds_fresh_brief(self) -> None:
        tmp, root = self.make_root("general")
        try:
            handoff = root / "handoff.zip"
            before = {
                "format": "agent-devtools-work-brief",
                "profile": {"id": "general"},
                "workingStateFingerprint": "before",
                "recovery": {"recommendedAction": "continue"},
            }
            with patch(
                "agent_devtools.handoff.create_preservation",
                side_effect=self.fake_preservation("workspace-snapshot"),
            ), patch(
                "agent_devtools.handoff.build_brief",
                return_value=before,
            ):
                create_handoff(root, out=handoff)

            fresh = {
                "format": "agent-devtools-work-brief",
                "profile": {"id": "general"},
                "workingStateFingerprint": "after",
                "recovery": {"recommendedAction": "continue"},
            }
            with patch(
                "agent_devtools.handoff.restore_preservation",
                return_value={"status": "pass", "kind": "workspace-snapshot"},
            ) as restore, patch(
                "agent_devtools.handoff.build_brief",
                return_value=fresh,
            ), patch(
                "agent_devtools.handoff.render_brief",
                return_value="AGENT RESUME",
            ):
                payload = resume_handoff(root, handoff)
            self.assertEqual("after", payload["currentWorkingStateFingerprint"])
            self.assertEqual("AGENT RESUME", payload["briefText"])
            self.assertTrue(restore.called)
        finally:
            tmp.cleanup()

    def test_manifest_tamper_is_rejected_without_duplicate_zip_members(self) -> None:
        tmp, root = self.make_root("general")
        try:
            out = root / "handoff.zip"
            with patch(
                "agent_devtools.handoff.create_preservation",
                side_effect=self.fake_preservation("workspace-snapshot"),
            ), patch(
                "agent_devtools.handoff.build_brief",
                return_value={
                    "format": "agent-devtools-work-brief",
                    "profile": {"id": "general"},
                    "workingStateFingerprint": "fp",
                    "recovery": {},
                },
            ):
                create_handoff(root, out=out)

            with zipfile.ZipFile(out, "r") as zf:
                entries = {name: zf.read(name) for name in zf.namelist()}
            entries["brief.txt"] = b"tampered\n"
            rewritten = out.with_suffix(".tampered.zip")
            with zipfile.ZipFile(rewritten, "w") as zf:
                for name, data in entries.items():
                    zf.writestr(name, data)
            with self.assertRaisesRegex(HandoffError, "hash mismatch"):
                inspect_handoff(rewritten)
        finally:
            tmp.cleanup()

    def test_duplicate_archive_members_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "duplicate.zip"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("handoff.json", "{}")
                zf.writestr("handoff.json", "{}")
            with self.assertRaisesRegex(HandoffError, "duplicate archive entries"):
                inspect_handoff(path)


if __name__ == "__main__":
    unittest.main()
