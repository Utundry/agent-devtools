from __future__ import annotations

import hashlib
import os
import shutil
import stat
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

MAX_ARCHIVE_ENTRIES = 10_000
MAX_ARCHIVE_ENTRY_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 1024 * 1024 * 1024


class ArchiveSafetyError(RuntimeError):
    pass


def _safe_name(name: str) -> str:
    value = str(name).replace("\\", "/")
    item = PurePosixPath(value)
    if not value or item.is_absolute() or ".." in item.parts:
        raise ArchiveSafetyError(f"unsafe archive path: {name!r}")
    normalized = item.as_posix()
    if normalized in {".", ""} or item.drive or (item.parts and ":" in item.parts[0]):
        raise ArchiveSafetyError(f"unsafe archive path: {name!r}")
    return normalized


def _validate_infos(
    infos: list[zipfile.ZipInfo],
    *,
    max_entries: int,
    max_entry_bytes: int,
    max_total_bytes: int,
) -> list[zipfile.ZipInfo]:
    files = [info for info in infos if not info.is_dir()]
    if len(files) > max_entries:
        raise ArchiveSafetyError(
            f"archive contains too many entries: {len(files)} > {max_entries}"
        )
    seen: set[str] = set()
    directories: set[str] = set()
    total = 0
    for info in files:
        name = _safe_name(info.filename)
        comparison = os.path.normcase(name)
        if comparison in seen:
            raise ArchiveSafetyError(f"archive contains duplicate archive entries: {name}")
        parents = [os.path.normcase(parent.as_posix()) for parent in PurePosixPath(name).parents if parent.as_posix() != "."]
        if comparison in directories or any(parent in seen for parent in parents):
            raise ArchiveSafetyError(f"archive file/directory collision: {name}")
        seen.add(comparison)
        directories.update(parents)
        if info.flag_bits & 0x1:
            raise ArchiveSafetyError(f"encrypted archive entry is not supported: {name}")
        if info.file_size < 0 or info.file_size > max_entry_bytes:
            raise ArchiveSafetyError(
                f"archive entry exceeds size limit ({max_entry_bytes} bytes): {name}"
            )
        total += int(info.file_size)
        if total > max_total_bytes:
            raise ArchiveSafetyError(
                f"archive uncompressed payload exceeds size limit ({max_total_bytes} bytes)"
            )
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise ArchiveSafetyError(f"archive symlink entry is not supported: {name}")
    return files


def read_zip_bounded(
    path: Path,
    *,
    max_entries: int = MAX_ARCHIVE_ENTRIES,
    max_entry_bytes: int = MAX_ARCHIVE_ENTRY_BYTES,
    max_total_bytes: int = MAX_ARCHIVE_TOTAL_BYTES,
) -> dict[str, bytes]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ArchiveSafetyError(f"archive not found: {path}")
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = _validate_infos(
                archive.infolist(),
                max_entries=max_entries,
                max_entry_bytes=max_entry_bytes,
                max_total_bytes=max_total_bytes,
            )
            payloads: dict[str, bytes] = {}
            for info in infos:
                name = _safe_name(info.filename)
                data = archive.read(info)
                if len(data) != info.file_size:
                    raise ArchiveSafetyError(
                        f"archive entry size changed while reading: {name}"
                    )
                payloads[name] = data
            return payloads
    except ArchiveSafetyError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ArchiveSafetyError(f"cannot read archive {path}: {exc}") from exc


def extract_zip_bounded(
    path: Path,
    destination: Path,
    *,
    max_entries: int = MAX_ARCHIVE_ENTRIES,
    max_entry_bytes: int = MAX_ARCHIVE_ENTRY_BYTES,
    max_total_bytes: int = MAX_ARCHIVE_TOTAL_BYTES,
) -> None:
    path = path.expanduser().resolve()
    destination = destination.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        raise ArchiveSafetyError(f"archive not found: {path}")
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = _validate_infos(
                archive.infolist(),
                max_entries=max_entries,
                max_entry_bytes=max_entry_bytes,
                max_total_bytes=max_total_bytes,
            )
            for info in infos:
                name = _safe_name(info.filename)
                target = (destination / name).resolve(strict=False)
                try:
                    target.relative_to(destination)
                except ValueError as exc:
                    raise ArchiveSafetyError(
                        f"archive path escapes extraction root: {name!r}"
                    ) from exc
                target.parent.mkdir(parents=True, exist_ok=True)
                temp = target.with_name(target.name + ".agent-archive.tmp")
                try:
                    with archive.open(info, "r") as source, temp.open("wb") as sink:
                        shutil.copyfileobj(source, sink, length=1024 * 1024)
                    if temp.stat().st_size != info.file_size:
                        raise ArchiveSafetyError(
                            f"archive entry size changed while extracting: {name}"
                        )
                    os.replace(temp, target)
                finally:
                    temp.unlink(missing_ok=True)
    except ArchiveSafetyError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ArchiveSafetyError(f"cannot extract archive {path}: {exc}") from exc


@contextmanager
def verified_zip(path: Path):
    """Validate a manifest in streaming passes before any consumer writes files."""
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = _validate_infos(archive.infolist(), max_entries=MAX_ARCHIVE_ENTRIES,
                                    max_entry_bytes=MAX_ARCHIVE_ENTRY_BYTES, max_total_bytes=MAX_ARCHIVE_TOTAL_BYTES)
            members = {_safe_name(info.filename): info for info in infos}
            manifest = members.get("MANIFEST.sha256")
            if manifest is None or manifest.file_size > 8 * 1024 * 1024:
                raise ArchiveSafetyError("archive is missing a bounded manifest")
            expected = {}
            for line in archive.read(manifest).decode("utf-8").splitlines():
                if not line.strip():
                    continue
                digest, raw = line.split("  ", 1)
                name = _safe_name(raw)
                if name in expected:
                    raise ArchiveSafetyError(f"archive manifest contains duplicate path: {name}")
                expected[name] = digest
            extras = set(members) - set(expected) - {"MANIFEST.sha256"}
            if extras:
                raise ArchiveSafetyError("archive contains unmanifested payload: " + ", ".join(sorted(extras)[:5]))
            for name, digest in expected.items():
                if name not in members:
                    raise ArchiveSafetyError(f"archive manifest references missing entry: {name}")
                calculated = hashlib.sha256()
                with archive.open(members[name]) as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        calculated.update(block)
                if calculated.hexdigest() != digest:
                    raise ArchiveSafetyError(f"archive payload hash mismatch: {name}")
            yield archive, members, expected
    except ArchiveSafetyError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError, UnicodeDecodeError) as exc:
        raise ArchiveSafetyError(f"cannot validate archive: {exc}") from exc


def read_metadata(archive, info, max_bytes=8 * 1024 * 1024) -> bytes:
    if info.file_size > max_bytes:
        raise ArchiveSafetyError(f"archive metadata exceeds limit: {info.filename}")
    return archive.read(info)
