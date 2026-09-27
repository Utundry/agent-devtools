from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


class ToolIdentityError(RuntimeError):
    pass


def executable(name: str) -> str:
    value = shutil.which(name)
    if not value:
        raise ToolIdentityError(f"Required executable not found in PATH: {name}")
    return value


def tool_identity(tool: str) -> str:
    tool = tool.strip()
    if tool == "python":
        return f"{sys.executable}|{sys.version}"
    if tool == "php":
        exe = executable("php")
        proc = subprocess.run(
            [exe, "-r", "echo PHP_VERSION;"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        return f"{exe}|{proc.stdout.strip()}|{proc.returncode}"
    if tool == "node":
        exe = executable("node")
        proc = subprocess.run(
            [exe, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        return f"{exe}|{proc.stdout.strip()}|{proc.returncode}"
    if tool.startswith("exe:"):
        name = tool.split(":", 1)[1]
        exe = executable(name)
        return f"{name}|{Path(exe).resolve()}"
    return tool
