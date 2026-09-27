from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from agent_devtools.check.replay import ReplayError, materialize_base


class ReleaseBaseDiscoveryTests(unittest.TestCase):
    def _zip_tree(self, source: Path, archive: Path) -> None:
        with zipfile.ZipFile(archive, "w") as zf:
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(source).as_posix())

    def test_unique_shallow_project_config_wins_over_vendored_tool_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            package = tmp / "package"
            root = package / "organizer-0.31.1.0"
            nested = root / "devtools" / "agent"
            nested.mkdir(parents=True)
            (root / "agent-tools.json").write_text("{}\n", encoding="utf-8")
            (nested / "agent-tools.json").write_text("{}\n", encoding="utf-8")
            (root / "app.txt").write_text("consumer\n", encoding="utf-8")
            archive = tmp / "base.zip"
            self._zip_tree(package, archive)

            destination = tmp / "extract"
            project_root = materialize_base(archive, destination)
            self.assertEqual(destination / "organizer-0.31.1.0", project_root)

    def test_equally_shallow_project_configs_remain_fail_safe(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            package = tmp / "package"
            for name in ("one", "two"):
                root = package / name
                root.mkdir(parents=True)
                (root / "agent-tools.json").write_text("{}\n", encoding="utf-8")
            archive = tmp / "base.zip"
            self._zip_tree(package, archive)

            with self.assertRaisesRegex(ReplayError, "equally shallow"):
                materialize_base(archive, tmp / "extract")


if __name__ == "__main__":
    unittest.main()
