from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_devtools.presets import apply_preset, get_preset
from agent_devtools.release.config import ReleaseConfigError, load_release_config


class ReleasePresetCompositionTests(unittest.TestCase):
    def test_python_preset_gets_source_release_without_copying_it_locally(self) -> None:
        preset = get_preset("python-stdlib")
        config = preset.files["agent-tools.json"]
        self.assertEqual(config["release"]["packages"], [{"id": "source", "kind": "source"}])
        raw = json.loads((preset.path).read_text(encoding="utf-8"))
        self.assertNotIn("release", raw["files"]["agent-tools.json"])
        self.assertIn("release-source", preset.components)

    def test_php_vue_vite_merges_atomic_runtime_contributions_by_package_id(self) -> None:
        preset = get_preset("php-vue-vite")
        packages = preset.files["agent-tools.json"]["release"]["packages"]
        self.assertEqual([row["id"] for row in packages], ["source", "runtime"])
        runtime = packages[1]
        self.assertEqual(runtime["kind"], "files")
        self.assertIn("backend/**", runtime["include"])
        self.assertIn("frontend/dist/**", runtime["include"])
        self.assertIn("frontend/dist/index.html", runtime["required"])
        self.assertEqual(len([row for row in packages if row["id"] == "runtime"]), 1)

    def test_vue_vite_release_runtime_is_declarative(self) -> None:
        preset = get_preset("vue-vite")
        packages = preset.files["agent-tools.json"]["release"]["packages"]
        self.assertEqual([row["id"] for row in packages], ["source", "runtime"])
        self.assertEqual(packages[1]["include"], ["dist/**"])
        self.assertEqual(packages[1]["required"], ["dist/index.html"])

    def test_omitted_artifact_prefix_defaults_to_project_directory_name(self) -> None:
        preset = get_preset("python-stdlib")
        with tempfile.TemporaryDirectory(prefix="agent-devtools-b3-") as td:
            project = Path(td) / "demo-project"
            project.mkdir()
            apply_preset(preset, project)
            config = load_release_config(project)
            self.assertEqual(config.artifact_prefix, "demo-project")
            self.assertEqual([item.package_id for item in config.packages], ["source"])

    def test_explicit_invalid_artifact_prefix_still_fails(self) -> None:
        preset = get_preset("python-stdlib")
        with tempfile.TemporaryDirectory(prefix="agent-devtools-b3-") as td:
            project = Path(td) / "demo-project"
            project.mkdir()
            apply_preset(preset, project)
            path = project / "agent-tools.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["release"]["artifactPrefix"] = "bad prefix!"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ReleaseConfigError):
                load_release_config(project)


if __name__ == "__main__":
    unittest.main()
