from __future__ import annotations

from pathlib import Path

from agent_devtools.changes import ChangeSetError, discover_changes


def git_changed_files(root: Path, base: str | None = None) -> set[str] | None:
    # Non-HEAD bases retain the historical Git comparison semantics because the
    # canonical change-set is defined against the current project HEAD.
    if base is not None and str(base).strip() and str(base).strip() != "HEAD":
        import shutil
        import subprocess
        root = root.resolve()
        if not (root / ".git").exists() or not shutil.which("git"):
            return None
        changed: set[str] = set()
        for command in (
            ["git", "diff", "--name-only", str(base).strip(), "--"],
            ["git", "ls-files", "--others", "--exclude-standard"],
        ):
            try:
                proc = subprocess.run(command, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", timeout=30)
            except subprocess.TimeoutExpired:
                return None
            if proc.returncode != 0:
                return None
            changed.update(line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip())
        return changed
    try:
        report = discover_changes(root)
    except ChangeSetError:
        return None
    if not report.get("available"):
        return None
    return set(str(item) for item in report.get("canonicalChangedFiles", []))
