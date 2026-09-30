from __future__ import annotations

import os
import shutil
import stat
import zipfile
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
    return value


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
    total = 0
    for info in files:
        name = _safe_name(info.filename)
        if name in seen:
            raise ArchiveSafetyError(f"archive contains duplicate archive entries: {name}")
        seen.add(name)
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
