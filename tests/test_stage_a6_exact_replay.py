from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from agent_devtools.check.config import load_check_config
from agent_devtools.check.replay import (
    ReplayError,
    apply_exact_bundle,
    canonical_source_hashes,
    compare_inventories,
    create_exact_bundle,
    run_exact_replay,
)
from agent_devtools.core.hashing import sha256_file
from agent_devtools.presets import get_preset


class ReplayHarness:
    @staticmethod
    def write_project(root: Path, *, value: str = "v1") -> None:
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "src/value.txt").write_text(value + "\n", encoding="utf-8")
        (root / "src/blob.bin").write_bytes(b"\x00\x01" + value.encode("ascii"))
        (root / "agent-check.policy.json").write_text(json.dumps({
            "format": "agent-devtools-check-policy",
            "formatVersion": 1,
            "features": {"source": {"affects": ["tests", "build"]}, "tests": {"affects": []}, "build": {"affects": []}},
            "sources": [{"patterns": ["src/**"], "impact": ["source"]}],
            "suites": {"tests": {"impact": ["tests"]}, "build": {"impact": ["build"]}},
            "profiles": {
                "affected": {"selection": "affected", "suites": ["tests", "build"], "always": []},
                "full": {"selection": "all", "suites": ["tests", "build"], "always": []},
            },
        }), encoding="utf-8")
        (root / "agent-tools.json").write_text(json.dumps({
            "version": 1,
            "ignore": [".agent-cache/**", ".agent-work/**", "dist/**", "**/__pycache__/**"],
            "check": {
                "policy": "agent-check.policy.json",
                "suiteOrder": ["tests", "build"],
                "guards": [],
                "dependencies": [],
                "commands": {
                    "tests": {
                        "argv": ["{python}", "-c", "from pathlib import Path; assert Path('src/value.txt').read_text().strip() in {'v1','v2'}"],
                        "inputs": ["src/**"],
                        "tool": "python",
                        "resultAdapter": "exit-code"
                    },
                    "build": {
                        "argv": ["{python}", "-c", "from pathlib import Path; p=Path('dist/out.txt'); p.parent.mkdir(exist_ok=True); p.write_text('artifact:' + Path('src/value.txt').read_text().strip())"],
                        "inputs": ["src/**"],
                        "tool": "python",
                        "resultAdapter": "exit-code",
                        "outputs": {"required": ["dist/out.txt"], "capture": ["dist/out.txt"]}
                    }
                },
                "certification": {
                    "profile": "full",
                    "reusableSuites": ["tests", "build"],
                    "globalInputs": ["agent-tools.json", "agent-check.policy.json"],
                    "evidencePath": ".agent-cache/check-certified-evidence-v1.json"
                },
                "replay": {
                    "sourceInclude": ["**"],
                    "sourceExclude": [],
                    "sharedPaths": []
                }
            }
        }), encoding="utf-8")

    @staticmethod
    def zip_project(root: Path, destination: Path) -> None:
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file() and ".agent-cache" not in path.parts and ".agent-work" not in path.parts and "dist" not in path.parts:
                    archive.write(path, f"project/{path.relative_to(root).as_posix()}")


class ExactReplayBundleTests(unittest.TestCase, ReplayHarness):
    def test_bundle_is_deterministic_binary_safe_and_replays_add_change_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base"
            target = root / "target"
            replay = root / "replay"
            base.mkdir(); target.mkdir()
            self.write_project(base, value="v1")
            shutil.copytree(base, target, dirs_exist_ok=True)
            (target / "src/value.txt").write_text("v2\n", encoding="utf-8")
            (target / "src/blob.bin").write_bytes(b"\x00\xffbinary-v2")
            (base / "src/delete-me.txt").write_text("old", encoding="utf-8")
            (target / "src/add-me.txt").write_text("new", encoding="utf-8")

            config = load_check_config(target)
            bundle1 = root / "one.agent-replay.zip"
            bundle2 = root / "two.agent-replay.zip"
            manifest1 = create_exact_bundle(base_root=base, target_root=target, config=config, bundle_path=bundle1)
            manifest2 = create_exact_bundle(base_root=base, target_root=target, config=config, bundle_path=bundle2)
            self.assertEqual(manifest1, manifest2)
            self.assertEqual(sha256_file(bundle1), sha256_file(bundle2))
            self.assertIn("src/add-me.txt", manifest1["delta"]["added"])
            self.assertIn("src/delete-me.txt", manifest1["delta"]["deleted"])
            self.assertIn("src/blob.bin", manifest1["delta"]["changed"])

            apply_exact_bundle(base_root=base, replay_root=replay, config=config, bundle_path=bundle1)
            comparison = compare_inventories(canonical_source_hashes(target, config), canonical_source_hashes(replay, config))
            self.assertEqual([], comparison["missing"])
            self.assertEqual([], comparison["extra"])
            self.assertEqual([], comparison["mismatch"])
            self.assertEqual((target / "src/blob.bin").read_bytes(), (replay / "src/blob.bin").read_bytes())

    def test_tampered_base_is_rejected_before_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base"; target = root / "target"; replay = root / "replay"
            base.mkdir(); target.mkdir()
            self.write_project(base, value="v1")
            shutil.copytree(base, target, dirs_exist_ok=True)
            (target / "src/value.txt").write_text("v2\n", encoding="utf-8")
            config = load_check_config(target)
            bundle = root / "change.agent-replay.zip"
            create_exact_bundle(base_root=base, target_root=target, config=config, bundle_path=bundle)
            (base / "src/value.txt").write_text("tampered\n", encoding="utf-8")
            with self.assertRaises(ReplayError):
                apply_exact_bundle(base_root=base, replay_root=replay, config=config, bundle_path=bundle)

    def test_full_replay_certifies_target_replay_and_generated_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_project = root / "base-project"
            target = root / "target"
            base_project.mkdir(); target.mkdir()
            self.write_project(base_project, value="v1")
            self.zip_project(base_project, root / "base.zip")
            shutil.copytree(base_project, target, dirs_exist_ok=True)
            (target / "src/value.txt").write_text("v2\n", encoding="utf-8")
            bundle = root / "exact.agent-replay.zip"

            code, report = run_exact_replay(
                target_root=target,
                base=root / "base.zip",
                bundle_out=bundle,
                source_only=False,
            )
            self.assertEqual(0, code, report)
            self.assertEqual("pass", report["status"])
            self.assertEqual("pass", report["targetCertification"]["status"])
            self.assertEqual("pass", report["replayCertification"]["status"])
            self.assertEqual([], report["source"]["mismatch"])
            self.assertEqual([], report["generated"]["mismatch"])
            self.assertEqual("artifact:v2", (target / "dist/out.txt").read_text(encoding="utf-8"))
            self.assertTrue(bundle.is_file())

    def test_source_only_replay_does_not_create_certification_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_project = root / "base-project"; target = root / "target"
            base_project.mkdir(); target.mkdir()
            self.write_project(base_project, value="v1")
            self.zip_project(base_project, root / "base.zip")
            shutil.copytree(base_project, target, dirs_exist_ok=True)
            (target / "src/value.txt").write_text("v2\n", encoding="utf-8")
            code, report = run_exact_replay(target_root=target, base=root / "base.zip", source_only=True)
            self.assertEqual(0, code, report)
            self.assertTrue(report["sourceOnly"])
            self.assertIsNone(report["targetCertification"])
            self.assertFalse((target / ".agent-cache").exists())


class ReplayPresetCompositionTests(unittest.TestCase):
    def test_php_vue_vite_reuses_atomic_shared_dependency_paths(self) -> None:
        preset = get_preset("php-vue-vite")
        replay = preset.files["agent-tools.json"]["check"]["replay"]
        self.assertEqual(["vendor", "frontend/node_modules"], replay["sharedPaths"])
        self.assertEqual(["**"], replay["sourceInclude"])

    def test_standalone_vue_vite_declares_generated_byte_capture(self) -> None:
        preset = get_preset("vue-vite")
        build = preset.files["agent-tools.json"]["check"]["commands"]["build"]
        self.assertEqual(["dist/index.html"], build["outputs"]["required"])
        self.assertEqual(["dist/index.html", "dist/assets/**"], build["outputs"]["capture"])


if __name__ == "__main__":
    unittest.main()
