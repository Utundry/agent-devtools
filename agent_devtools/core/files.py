from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Iterator

from .hashing import sha256_file
from .pathmatch import matches_any


def iter_paths(root: Path, patterns: Iterable[str] = ("**",), *, exclude: Iterable[str] = (), files_only: bool = True) -> Iterator[Path]:
    """Scan fixed glob prefixes once, pruning excluded directories before descent.

    Contracts provide their own exclusions: outputs must remain visible even when
    they are excluded from source inventories. Symlinks are yielded but not followed;
    the caller decides whether to reject or omit them.
    """
    root = root.resolve()
    patterns, exclude = tuple(patterns), tuple(exclude)
    if not patterns:
        return
    bases: set[Path] = set()
    literals: set[Path] = set()
    for pattern in patterns:
        parts = pattern.replace("\\", "/").split("/")
        if Path(pattern).is_absolute() or ".." in parts:
            continue
        prefix = []
        for part in parts:
            if any(char in part for char in "*?["):
                break
            prefix.append(part)
        if len(prefix) == len(parts):
            literals.add(root.joinpath(*prefix))
        else:
            bases.add(root.joinpath(*prefix))
    minimal = sorted((base for base in bases if not any(other != base and other in base.parents for other in bases)), key=str)
    seen: set[Path] = set()

    def eligible(path: Path) -> bool:
        rel = path.relative_to(root).as_posix()
        return (not matches_any(rel, exclude) and matches_any(rel, patterns)
                and (not files_only or path.is_file()))

    for path in sorted(literals, key=str):
        if path.exists() and eligible(path):
            seen.add(path)
            yield path
    for base in minimal:
        rel = base.relative_to(root).as_posix()
        if matches_any(rel, exclude) or base.is_symlink() or not base.is_dir():
            continue
        if base != root and base not in seen and eligible(base):
            seen.add(base)
            yield base
        for directory, directories, filenames in os.walk(base, followlinks=False):
            parent = Path(directory)
            names = sorted((*directories, *filenames)) if not files_only else sorted(filenames + [name for name in directories if (parent / name).is_symlink()])
            for name in names:
                path = parent / name
                if path not in seen and eligible(path):
                    seen.add(path)
                    yield path
            directories[:] = sorted(name for name in directories if not (parent / name).is_symlink()
                                    and not matches_any((parent / name).relative_to(root).as_posix(), exclude))


class FileInventory:
    """Disposable per-run paths/hashes. Invalidate after an executed stage."""
    def __init__(self, root: Path, exclude: Iterable[str] = ()) -> None:
        self.root, self.exclude = root.resolve(), tuple(exclude)
        self._paths: list[Path] | None = None
        self._hashes: dict[str, str] = {}

    def invalidate(self) -> None:
        self._paths = None
        self._hashes.clear()

    def hashes(self, patterns: Iterable[str], extra: Iterable[Path] = ()) -> dict[str, str]:
        patterns = tuple(patterns)
        if self._paths is None:
            self._paths = list(iter_paths(self.root, exclude=self.exclude))
        paths = {path.relative_to(self.root).as_posix(): path for path in self._paths
                 if not path.is_symlink() and matches_any(path.relative_to(self.root).as_posix(), patterns)}
        paths.update({path.relative_to(self.root).as_posix(): path for path in extra})
        for rel, path in paths.items():
            if rel not in self._hashes:
                self._hashes[rel] = sha256_file(path)
        return {rel: self._hashes[rel] for rel in sorted(paths)}
