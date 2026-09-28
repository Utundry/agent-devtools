from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import zipfile
import sys
from io import BytesIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_bootstrap", ROOT / "scripts" / "build_bootstrap.py")
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = builder
SPEC.loader.exec_module(builder)


class BootstrapBuilderTests(unittest.TestCase):
    def _template(self, version: str = "0.7.0.1") -> bytes:
        entries = {
            "AGENT-READ-ME-FIRST.md": f"Agent DevTools {version}\n".encode(),
            "BOOTSTRAP_AGENT_DEVTOOLS.py": f'VERSION = "{version}"\n'.encode(),
            "bootstrap-kit.json": json.dumps({
                "format": "agent-devtools-bootstrap-kit",
                "version": version,
                "payloadFiles": 1,
                "payloadFingerprint": "0" * 64,
            }).encode(),
            "payload/agent.py": b"old\n",
        }
        entries["MANIFEST.sha256"] = builder._manifest_bytes(entries)
        return builder._deterministic_zip(entries)

    def test_single_file_installer_round_trips_exact_kit(self) -> None:
        kit = b"PK synthetic exact bytes"
        installer = builder._single_file_installer("0.8.0", kit)
        name, digest, embedded = builder._embedded_kit(installer)
        self.assertEqual("agent-devtools-integration-update-0.8.0-minimal.zip", name)
        self.assertEqual(builder.sha256_bytes(kit), digest)
        self.assertEqual(kit, embedded)

    def test_deterministic_zip_is_byte_stable(self) -> None:
        entries = {"z.txt": b"z", "a.txt": b"a"}
        self.assertEqual(builder._deterministic_zip(entries), builder._deterministic_zip(dict(reversed(list(entries.items())))))

    def test_version_inference_rejects_ambiguous_template(self) -> None:
        entries = builder._read_kit_entries(self._template())
        entries["AGENT-READ-ME-FIRST.md"] += b"other 0.6.0\n"
        with self.assertRaisesRegex(builder.BootstrapBuildError, "cannot infer one previous"):
            builder._infer_old_version(entries, "0.8.0")

    def test_manifest_covers_every_non_manifest_entry(self) -> None:
        entries = {"a": b"A", "b": b"B"}
        manifest = builder._manifest_bytes(entries).decode()
        self.assertIn(builder.sha256_bytes(b"A") + "  a", manifest)
        self.assertIn(builder.sha256_bytes(b"B") + "  b", manifest)

    def test_metadata_refreshes_only_exact_known_values(self) -> None:
        updated = builder._replace_exact_metadata_values(
            {"version": "0.7.0.1", "payloadFiles": 10, "payloadFingerprint": "a" * 64, "note": "keep"},
            old_version="0.7.0.1", new_version="0.8.0",
            old_fp="a" * 64, new_fp="b" * 64,
            old_count=10, new_count=12,
        )
        self.assertEqual("0.8.0", updated["version"])
        self.assertEqual(12, updated["payloadFiles"])
        self.assertEqual("b" * 64, updated["payloadFingerprint"])
        self.assertEqual("keep", updated["note"])

    def test_porcelain_dirty_paths_allow_exact_generated_bootstrap_outputs(self) -> None:
        status = (
            " M bootstrap/AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py\n"
            " M bootstrap/agent-devtools-bootstrap-kit.zip\n"
        )
        paths = builder._dirty_paths_from_porcelain(status)
        self.assertEqual(
            (
                "bootstrap/AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py",
                "bootstrap/agent-devtools-bootstrap-kit.zip",
            ),
            paths,
        )

    def test_porcelain_dirty_paths_expose_non_generated_source_changes(self) -> None:
        status = (
            " M bootstrap/AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py\n"
            " M agent_devtools/work/state.py\n"
        )
        paths = builder._dirty_paths_from_porcelain(status)
        allowed = {
            "bootstrap/AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py",
            "bootstrap/agent-devtools-bootstrap-kit.zip",
        }
        self.assertEqual(["agent_devtools/work/state.py"], [path for path in paths if path not in allowed])


if __name__ == "__main__":
    unittest.main()
