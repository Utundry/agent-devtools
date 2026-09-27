from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from agent_devtools.core.hashing import sha256_file
from agent_devtools.release.build import build_release, verify_release
from agent_devtools.release.config import ReleaseConfigError, load_release_config
from agent_devtools.release.package import PackageError, package_inventory, verify_package_manifest
from agent_devtools.check.config import load_check_config


class ReleaseHarness:
    @staticmethod
    def write_project(root: Path, *, value: str) -> None:
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "src/value.txt").write_text(value + "\n", encoding="utf-8")
        (root / "agent-check.policy.json").write_text(json.dumps({
            "format": "agent-devtools-check-policy",
            "formatVersion": 1,
            "features": {"source": {"affects": ["tests", "build"]}, "tests": {"affects": []}, "build": {"affects": []}},
            "sources": [{"patterns": ["src/**"], "impact": ["source"]}],
            "suites": {"tests": {"impact": ["tests"]}, "build": {"impact": ["build"]}},
            "profiles": {
                "affected": {"selection": "affected", "suites": ["tests", "build"], "always": []},
                "full": {"selection": "all", "suites": ["tests", "build"], "always": []}
            }
        }), encoding="utf-8")
        (root / "agent-tools.json").write_text(json.dumps({
            "version": 1,
            "ignore": [".agent-cache/**", ".agent-work/**", "dist/**"],
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
                        "argv": ["{python}", "-c", "from pathlib import Path; p=Path('dist/out.txt'); p.parent.mkdir(exist_ok=True); p.write_text('runtime:' + Path('src/value.txt').read_text().strip())"],
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
                "replay": {"sourceInclude": ["**"], "sourceExclude": [], "sharedPaths": []}
            },
            "release": {
                "artifactPrefix": "sample-app",
                "packages": [
                    {"id": "source", "kind": "source"},
                    {"id": "runtime", "kind": "files", "include": ["dist/**"], "required": ["dist/out.txt"]}
                ]
            }
        }), encoding="utf-8")

    @staticmethod
    def zip_project(root: Path, destination: Path) -> None:
        with zipfile.ZipFile(destination, "w") as z:
            for path in sorted(root.rglob("*")):
                if path.is_file() and ".agent-cache" not in path.parts and ".agent-work" not in path.parts and "dist" not in path.parts:
                    z.write(path, f"project/{path.relative_to(root).as_posix()}")


class ReleaseFoundationTests(unittest.TestCase, ReleaseHarness):
    def test_build_creates_source_runtime_replay_evidence_and_verify_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base"; target = root / "target"; out = root / "release"
            base.mkdir(); target.mkdir()
            self.write_project(base, value="v1")
            self.zip_project(base, root / "base.zip")
            shutil.copytree(base, target, dirs_exist_ok=True)
            (target / "src/value.txt").write_text("v2\n", encoding="utf-8")

            code, evidence = build_release(root=target, version="2.0.0", base=root / "base.zip", out_dir=out)
            self.assertEqual(0, code, evidence)
            self.assertEqual("pass", evidence["status"])
            self.assertEqual(["source", "runtime"], [row["id"] for row in evidence["packages"]])
            self.assertEqual("runtime:v2", (target / "dist/out.txt").read_text(encoding="utf-8"))
            self.assertTrue((out / "sample-app-2.0.0-source.zip").is_file())
            self.assertTrue((out / "sample-app-2.0.0-runtime.zip").is_file())
            self.assertTrue((out / "sample-app-2.0.0.agent-replay.zip").is_file())
            self.assertEqual(0, verify_release(out)[0])

    def test_source_package_embeds_deterministic_payload_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base"; target = root / "target"; out = root / "release"
            base.mkdir(); target.mkdir()
            self.write_project(base, value="v1")
            self.zip_project(base, root / "base.zip")
            shutil.copytree(base, target, dirs_exist_ok=True)
            (target / "src/value.txt").write_text("v2\n", encoding="utf-8")

            code, evidence = build_release(root=target, version="2.0.0", base=root / "base.zip", out_dir=out)
            self.assertEqual(0, code, evidence)
            source = out / "sample-app-2.0.0-source.zip"
            with zipfile.ZipFile(source) as archive:
                root_name = archive.namelist()[0].split("/", 1)[0]
                raw = archive.read(f"{root_name}/MANIFEST.sha256").decode("utf-8")
                self.assertTrue(raw.startswith("# agent-devtools-source-package-manifest-v1\n"))
                manifest_lines = [line for line in raw.splitlines() if line and not line.startswith("#")]
                self.assertGreater(len(manifest_lines), 0)
                package_manifest = json.loads(
                    (out / evidence["packages"][0]["manifest"]).read_text(encoding="utf-8")
                )
                self.assertEqual(
                    sorted(package_manifest["content"]),
                    sorted(line.split("  ", 1)[1] for line in manifest_lines),
                )

    def test_embedded_source_manifest_tamper_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base"; target = root / "target"; out = root / "release"
            base.mkdir(); target.mkdir()
            self.write_project(base, value="v1")
            self.zip_project(base, root / "base.zip")
            shutil.copytree(base, target, dirs_exist_ok=True)
            (target / "src/value.txt").write_text("v2\n", encoding="utf-8")
            self.assertEqual(0, build_release(root=target, version="2.0.0", base=root / "base.zip", out_dir=out)[0])

            outer = json.loads((out / "sample-app-2.0.0-source.zip.manifest.json").read_text(encoding="utf-8"))
            source = out / outer["archive"]
            rewritten = source.with_suffix(".tampered.zip")
            with zipfile.ZipFile(source) as incoming, zipfile.ZipFile(rewritten, "w") as outgoing:
                for info in incoming.infolist():
                    data = incoming.read(info.filename)
                    if info.filename.endswith("/MANIFEST.sha256"):
                        data += b"0" * 64 + b"  forged.txt\n"
                    outgoing.writestr(info, data)
            rewritten.replace(source)
            outer["archiveSha256"] = sha256_file(source)
            failures = verify_package_manifest(out, outer)
            self.assertTrue(any("embedded MANIFEST.sha256 content mismatch" in item for item in failures), failures)

    def test_release_artifacts_are_deterministic_for_identical_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base"; target = root / "target"
            base.mkdir(); target.mkdir()
            self.write_project(base, value="v1")
            self.zip_project(base, root / "base.zip")
            shutil.copytree(base, target, dirs_exist_ok=True)
            (target / "src/value.txt").write_text("v2\n", encoding="utf-8")
            out1 = root / "r1"; out2 = root / "r2"
            self.assertEqual(0, build_release(root=target, version="2.0.0", base=root / "base.zip", out_dir=out1)[0])
            self.assertEqual(0, build_release(root=target, version="2.0.0", base=root / "base.zip", out_dir=out2)[0])
            for name in [
                "sample-app-2.0.0-source.zip",
                "sample-app-2.0.0-runtime.zip",
                "sample-app-2.0.0.agent-replay.zip",
                "RELEASE-EVIDENCE.json",
                "SHA256SUMS.txt",
            ]:
                self.assertEqual(sha256_file(out1 / name), sha256_file(out2 / name), name)


    def test_source_package_is_a_valid_base_for_the_next_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base"; target = root / "target"; out1 = root / "r1"; out2 = root / "r2"
            base.mkdir(); target.mkdir()
            self.write_project(base, value="v1")
            self.zip_project(base, root / "base.zip")
            shutil.copytree(base, target, dirs_exist_ok=True)
            (target / "src/value.txt").write_text("v2\n", encoding="utf-8")
            self.assertEqual(0, build_release(root=target, version="2.0.0", base=root / "base.zip", out_dir=out1)[0])
            source_base = out1 / "sample-app-2.0.0-source.zip"
            code, evidence = build_release(root=target, version="2.0.1", base=source_base, out_dir=out2)
            self.assertEqual(0, code, evidence)
            self.assertEqual([], evidence["replay"]["source"]["mismatch"])
            self.assertEqual(0, evidence["replay"]["source"]["missing"] and 1 or 0)
            self.assertEqual(0, evidence["replay"]["source"]["extra"] and 1 or 0)

    def test_verify_detects_archive_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base"; target = root / "target"; out = root / "release"
            base.mkdir(); target.mkdir()
            self.write_project(base, value="v1")
            self.zip_project(base, root / "base.zip")
            shutil.copytree(base, target, dirs_exist_ok=True)
            (target / "src/value.txt").write_text("v2\n", encoding="utf-8")
            self.assertEqual(0, build_release(root=target, version="2.0.0", base=root / "base.zip", out_dir=out)[0])
            with (out / "sample-app-2.0.0-runtime.zip").open("ab") as f:
                f.write(b"tamper")
            code, report = verify_release(out)
            self.assertEqual(1, code)
            self.assertTrue(any("archive hash mismatch" in item or "SHA256SUMS mismatch" in item for item in report["failures"]))

    def test_missing_required_runtime_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_project(root, value="v1")
            release = load_release_config(root)
            check = load_check_config(root)
            runtime = next(item for item in release.packages if item.package_id == "runtime")
            with self.assertRaises(PackageError):
                package_inventory(root, runtime, check)

    def test_invalid_release_prefix_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_project(root, value="v1")
            data = json.loads((root / "agent-tools.json").read_text(encoding="utf-8"))
            data["release"]["artifactPrefix"] = "bad prefix"
            (root / "agent-tools.json").write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ReleaseConfigError):
                load_release_config(root)


if __name__ == "__main__":
    unittest.main()
