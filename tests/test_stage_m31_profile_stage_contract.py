from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_devtools.bootstrap import _iter_vendor_files
from agent_devtools.cli import parser as cli_parser
from agent_devtools.work.context_projection import context_stage_choices, current_context

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SOURCE = PROJECT_ROOT / "bootstrap" / "BOOTSTRAP_AGENT_DEVTOOLS.py"


class ProfileAndStageContractTests(unittest.TestCase):
    def make_kit(self, temp_root: Path) -> Path:
        kit = temp_root / "kit"
        payload = kit / "payload"
        payload.mkdir(parents=True)
        shutil.copy2(BOOTSTRAP_SOURCE, kit / BOOTSTRAP_SOURCE.name)
        for rel, source in _iter_vendor_files(PROJECT_ROOT):
            target = payload / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return kit / BOOTSTRAP_SOURCE.name

    def run_bootstrap(self, launcher: Path, target: Path, *extra: str) -> dict:
        proc = subprocess.run(
            [sys.executable, str(launcher), "--target", str(target), *extra, "--json"],
            cwd=target,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def test_explicit_neutral_profile_wins_over_inferred_document_profile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); launcher = self.make_kit(base); target = base / "workspace"; target.mkdir()
            report = self.run_bootstrap(launcher, target, "--profile", "research", "--intent", "Write a proposal text")
            config = json.loads((target / "agent-tools.json").read_text(encoding="utf-8"))
            self.assertEqual("document", report["intentDetection"]["profile"])
            self.assertNotIn("specialization", report)
            self.assertEqual("research", config["work"]["profile"])

    def test_inference_still_specializes_when_profile_is_not_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); launcher = self.make_kit(base); target = base / "workspace"; target.mkdir()
            report = self.run_bootstrap(launcher, target, "--intent", "Write a proposal text")
            config = json.loads((target / "agent-tools.json").read_text(encoding="utf-8"))
            self.assertEqual("document", report["intentDetection"]["profile"])
            self.assertEqual("document", report["specialization"]["toProfile"])
            self.assertEqual("document", config["work"]["profile"])

    def test_explicit_neutral_profile_does_not_require_intent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); launcher = self.make_kit(base); target = base / "workspace"; target.mkdir()
            report = self.run_bootstrap(launcher, target, "--profile", "research")
            config = json.loads((target / "agent-tools.json").read_text(encoding="utf-8"))
            self.assertEqual("ready", report["status"])
            self.assertEqual("research", config["work"]["profile"])

    def test_explicit_neutral_profile_can_respecialize_existing_neutral_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); launcher = self.make_kit(base); target = base / "workspace"; target.mkdir()
            self.run_bootstrap(launcher, target, "--profile", "document")
            report = self.run_bootstrap(launcher, target, "--profile", "research", "--intent", "Write a proposal text")
            config = json.loads((target / "agent-tools.json").read_text(encoding="utf-8"))
            self.assertEqual("research", report["specialization"]["toProfile"])
            self.assertEqual("research", config["work"]["profile"])

    def test_context_parser_accepts_every_projection_stage_alias(self) -> None:
        stages = context_stage_choices()
        self.assertIn("orient", stages); self.assertIn("verify", stages)
        for stage in stages:
            args = cli_parser().parse_args(["context", "current", "--stage", stage, "--json"])
            self.assertEqual(stage, args.stage)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "agent-tools.json").write_text(json.dumps({"version": 1, "work": {"profile": "general"}}), encoding="utf-8")
            self.assertEqual("planning", current_context(root, stage="orient")["operational"]["stage"])
            self.assertEqual("verification", current_context(root, stage="verify")["operational"]["stage"])


if __name__ == "__main__":
    unittest.main()
