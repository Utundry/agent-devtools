from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_devtools.cli import main as cli_main, parser


class ShellTopLevelDispatchTests(unittest.TestCase):
    def test_shell_option_does_not_overwrite_top_level_command_dest(self) -> None:
        args = parser().parse_args([
            "shell",
            "--command", "status",
            "--command", "finish",
            "--no-banner",
        ])
        self.assertEqual("shell", args.command)
        self.assertEqual(["status", "finish"], args.shell_commands)
        self.assertTrue(args.no_banner)

    def test_real_top_level_shell_batch_reaches_existing_dispatcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("agent_devtools.cli.discover_project_root", return_value=root):
                with patch("builtins.print"):
                    status = cli_main([
                        "shell",
                        "--command", "doctor --json",
                        "--no-banner",
                    ])
            self.assertEqual(0, status)


if __name__ == "__main__":
    unittest.main()
