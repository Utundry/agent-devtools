from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from agent_devtools.core.files import iter_paths
from agent_devtools.core.hashing import sha256_file
from agent_devtools.core.pathmatch import matches_any, matches_pattern

from .chunks import ChunkError, chunk_text, semantic_chunk
from .config import ContextConfig
from .relations import build_definition_index, extract_relations
from .semantic_map import SemanticMapError, load_semantic_map, resolve_mapping

SCHEMA_VERSION = 2


class ContextIndexError(RuntimeError):
    pass


@dataclass(frozen=True)
class FileInfo:
    path: str
    sha256: str
    size: int
    source_kind: str
    weight: float
    priority: str
    is_text: bool


def fts5_available() -> bool:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE _fts_probe USING fts5(content)")
        return True
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()


def _source_kind(config: ContextConfig, rel: str) -> tuple[str, float]:
    if rel.startswith(".agent-knowledge/"):
        return "knowledge", 1.35
    matches = [rule for rule in config.source_kinds if matches_pattern(rel, rule.glob)]
    if not matches:
        return "source", 1.0
    best = max(matches, key=lambda rule: rule.weight)
    return best.kind, best.weight


def _superseded_knowledge_paths(config: ContextConfig) -> set[str]:
    base = config.root / ".agent-knowledge"
    if not base.is_dir():
        return set()
    by_id: dict[str, str] = {}
    superseded: set[str] = set()
    for path in sorted(base.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict) or payload.get("format") != "agent-devtools-project-knowledge":
            continue
        record_id = str(payload.get("id") or "").strip()
        if record_id:
            by_id[record_id] = path.relative_to(config.root).as_posix()
        for target in payload.get("supersedes", []) if isinstance(payload.get("supersedes"), list) else []:
            value = str(target).strip()
            if value:
                superseded.add(value)
    return {by_id[item] for item in superseded if item in by_id}


def _file_info(config: ContextConfig, path: Path, rel: str, *, force: bool = False, superseded_knowledge: set[str] | None = None) -> FileInfo | None:
    if path.is_symlink() or not path.is_file() or matches_any(rel, config.exclude):
        return None
    if not force and not matches_any(rel, config.include):
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > config.max_file_bytes:
        return None
    try:
        with path.open("rb") as handle:
            sample = handle.read(min(size, 4096))
    except OSError:
        return None
    is_text = b"\x00" not in sample
    kind, weight = _source_kind(config, rel)
    if superseded_knowledge and rel in superseded_knowledge:
        kind, weight = "history", 0.25
    priority = "low" if matches_any(rel, config.low_priority) or kind == "history" else "normal"
    return FileInfo(rel, sha256_file(path), size, kind, weight, priority, is_text)


def discover_files(config: ContextConfig) -> dict[str, FileInfo]:
    semantic_map = load_semantic_map(config.root, config.semantic_map)
    forced = set(semantic_map.target_files)
    result: dict[str, FileInfo] = {}
    superseded_knowledge = _superseded_knowledge_paths(config)
    for path in iter_paths(config.root, exclude=config.exclude):
        rel = path.relative_to(config.root).as_posix()
        info = _file_info(config, path, rel, force=rel in forced, superseded_knowledge=superseded_knowledge)
        if info is not None and (info.is_text or rel in forced):
            result[rel] = info
    return result


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _create_schema(
    conn: sqlite3.Connection,
    *,
    use_fts: bool,
    config_fingerprint: str,
    semantic_map_fingerprint: str,
    semantic_map_targets: tuple[str, ...],
) -> None:
    conn.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE files (
            path TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            size INTEGER NOT NULL,
            source_kind TEXT NOT NULL,
            weight REAL NOT NULL,
            priority TEXT NOT NULL,
            synopsis TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE chunks (
            chunk_id TEXT PRIMARY KEY,
            path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            label TEXT NOT NULL,
            anchor TEXT UNIQUE,
            selector TEXT,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            content TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            weight REAL NOT NULL,
            priority TEXT NOT NULL
        );
        CREATE INDEX chunks_path_idx ON chunks(path);
        CREATE INDEX chunks_label_idx ON chunks(label);
        CREATE INDEX chunks_anchor_idx ON chunks(anchor);
        CREATE TABLE file_relations (
            source_path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
            target_path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
            relation TEXT NOT NULL,
            PRIMARY KEY(source_path, target_path, relation)
        );
        CREATE INDEX file_relations_target_idx ON file_relations(target_path);
    """)
    if use_fts:
        conn.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, path, label, content, tokenize='unicode61 remove_diacritics 2')")
    conn.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", [
        ("schema_version", str(SCHEMA_VERSION)),
        ("config_fingerprint", config_fingerprint),
        ("semantic_map_fingerprint", semantic_map_fingerprint),
        ("semantic_map_targets", json.dumps(list(semantic_map_targets), sort_keys=True)),
        ("fts5", "1" if use_fts else "0"),
    ])
    conn.commit()


def _db_meta(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        return {str(row[0]): str(row[1]) for row in conn.execute("SELECT key, value FROM meta")}
    except sqlite3.DatabaseError:
        return {}


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _delete_file(conn: sqlite3.Connection, rel: str, use_fts: bool) -> None:
    if use_fts:
        ids = [row[0] for row in conn.execute("SELECT chunk_id FROM chunks WHERE path=?", (rel,))]
        conn.executemany("DELETE FROM chunks_fts WHERE chunk_id=?", ((chunk_id,) for chunk_id in ids))
    conn.execute("DELETE FROM files WHERE path=?", (rel,))


def _insert_chunk(conn: sqlite3.Connection, chunk, info: FileInfo, use_fts: bool) -> None:
    try:
        conn.execute(
            "INSERT INTO chunks(chunk_id,path,kind,label,anchor,selector,start_line,end_line,content,content_sha256,source_kind,weight,priority) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                chunk.chunk_id, chunk.path, chunk.kind, chunk.label, chunk.anchor, chunk.selector,
                chunk.start_line, chunk.end_line, chunk.content, chunk.content_sha256,
                info.source_kind, info.weight, info.priority,
            ),
        )
    except sqlite3.IntegrityError as exc:
        if chunk.anchor:
            raise ContextIndexError(f"duplicate semantic identity across project: {chunk.anchor}") from exc
        raise ContextIndexError(f"duplicate stable context identity: {chunk.chunk_id}") from exc
    if use_fts:
        conn.execute(
            "INSERT INTO chunks_fts(chunk_id,path,label,content) VALUES (?,?,?,?)",
            (chunk.chunk_id, chunk.path, chunk.label, chunk.content),
        )


def _index_file(conn: sqlite3.Connection, config: ContextConfig, info: FileInfo, use_fts: bool, semantic_map) -> int:
    text = _read_text(config.root / info.path) if info.is_text else None
    regular = []
    if text is not None:
        try:
            regular = chunk_text(info.path, text, chunk_lines=config.chunk_lines, overlap_lines=config.overlap_lines)
        except ChunkError as exc:
            raise ContextIndexError(str(exc)) from exc
    mapped = []
    for mapping in semantic_map.mappings:
        if mapping.file != info.path:
            continue
        try:
            content, start, end = resolve_mapping(config.root, mapping)
        except SemanticMapError as exc:
            raise ContextIndexError(str(exc)) from exc
        mapped.append(semantic_chunk(
            info.path,
            mapping.semantic_id,
            f"{mapping.selector_type}:{mapping.selector_value}",
            content,
            start,
            end,
        ))
    conn.execute(
        "INSERT INTO files(path, sha256, size, source_kind, weight, priority, synopsis) VALUES (?, ?, ?, ?, ?, ?, '')",
        (info.path, info.sha256, info.size, info.source_kind, info.weight, info.priority),
    )
    all_chunks = [*regular, *mapped]
    for chunk in all_chunks:
        _insert_chunk(conn, chunk, info, use_fts)
    synopsis_parts = [info.path, info.source_kind]
    for chunk in all_chunks:
        synopsis_parts.extend((chunk.kind, chunk.label))
        if chunk.anchor:
            synopsis_parts.append(chunk.anchor)
        if chunk.selector:
            synopsis_parts.append(chunk.selector)
    if text is not None:
        # Cheap file-first lexical synopsis: unique terms only, bounded so the file table
        # stays small while content-only queries can still select the right file.
        lexical: list[str] = []
        seen_terms: set[str] = set()
        for token in re.findall(r"[\w.-]{2,}", text.casefold(), flags=re.UNICODE):
            if token in seen_terms:
                continue
            seen_terms.add(token)
            lexical.append(token)
            if len(lexical) >= 1024:
                break
        synopsis_parts.extend(lexical)
    conn.execute("UPDATE files SET synopsis=? WHERE path=?", ("\n".join(synopsis_parts), info.path))
    return len(all_chunks)


def _rebuild_relations(conn: sqlite3.Connection, config: ContextConfig, files: dict[str, FileInfo]) -> None:
    conn.execute("DELETE FROM file_relations")
    known = set(files)
    texts: dict[str, str] = {}
    for rel, info in files.items():
        if info.is_text:
            text = _read_text(config.root / rel)
            if text is not None:
                texts[rel] = text
    definition_index = build_definition_index(texts)
    for rel, text in texts.items():
        for target, relation in sorted(extract_relations(rel, text, known, definition_index=definition_index)):
            if target != rel:
                conn.execute(
                    "INSERT OR IGNORE INTO file_relations(source_path,target_path,relation) VALUES (?,?,?)",
                    (rel, target, relation),
                )


def _rebuild(config: ContextConfig, files: dict[str, FileInfo], *, use_fts: bool, semantic_map) -> dict:
    started = time.monotonic()
    db = config.database
    db.parent.mkdir(parents=True, exist_ok=True)
    building = db.with_name(db.name + ".building")
    building.unlink(missing_ok=True)
    conn = _connect(building)
    try:
        _create_schema(
            conn,
            use_fts=use_fts,
            config_fingerprint=config.fingerprint,
            semantic_map_fingerprint=semantic_map.fingerprint,
            semantic_map_targets=semantic_map.target_files,
        )
        chunks = 0
        for info in files.values():
            chunks += _index_file(conn, config, info, use_fts, semantic_map)
        _rebuild_relations(conn, config, files)
        conn.commit()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ContextIndexError(f"SQLite integrity_check failed: {integrity}")
    finally:
        conn.close()
    os.replace(building, db)
    return {
        "mode": "rebuild",
        "files": len(files),
        "chunks": chunks,
        "changed": len(files),
        "removed": 0,
        "semanticMapChanged": False,
        "fts5": use_fts,
        "elapsedSeconds": round(time.monotonic() - started, 6),
    }


def ensure_index(config: ContextConfig, *, rebuild: bool = False, force_no_fts: bool = False) -> dict:
    started = time.monotonic()
    try:
        semantic_map = load_semantic_map(config.root, config.semantic_map)
        files = discover_files(config)
    except SemanticMapError as exc:
        raise ContextIndexError(str(exc)) from exc
    use_fts = fts5_available() and not force_no_fts
    db = config.database
    building = db.with_name(db.name + ".building")
    if building.exists() and not db.exists():
        building.unlink(missing_ok=True)
    if rebuild or not db.is_file():
        return _rebuild(config, files, use_fts=use_fts, semantic_map=semantic_map)

    conn = None
    try:
        conn = _connect(db)
        meta = _db_meta(conn)
        valid = (
            meta.get("schema_version") == str(SCHEMA_VERSION)
            and meta.get("config_fingerprint") == config.fingerprint
            and meta.get("fts5") == ("1" if use_fts else "0")
        )
        if not valid:
            conn.close()
            conn = None
            return _rebuild(config, files, use_fts=use_fts, semantic_map=semantic_map)

        previous = {row["path"]: row["sha256"] for row in conn.execute("SELECT path, sha256 FROM files")}
        removed = sorted(set(previous) - set(files))
        changed_set = {rel for rel, info in files.items() if previous.get(rel) != info.sha256}
        semantic_map_changed = meta.get("semantic_map_fingerprint") != semantic_map.fingerprint
        if semantic_map_changed:
            try:
                old_targets = set(json.loads(meta.get("semantic_map_targets", "[]")))
            except json.JSONDecodeError:
                old_targets = set()
            changed_set.update(old_targets & set(files))
            changed_set.update(set(semantic_map.target_files) & set(files))
        changed = sorted(changed_set)
        if removed or changed or semantic_map_changed:
            with conn:
                for rel in removed:
                    _delete_file(conn, rel, use_fts)
                for rel in changed:
                    if rel in previous:
                        _delete_file(conn, rel, use_fts)
                    _index_file(conn, config, files[rel], use_fts, semantic_map)
                _rebuild_relations(conn, config, files)
                conn.execute("UPDATE meta SET value=? WHERE key='semantic_map_fingerprint'", (semantic_map.fingerprint,))
                conn.execute("UPDATE meta SET value=? WHERE key='semantic_map_targets'", (json.dumps(list(semantic_map.target_files), sort_keys=True),))
        counts = conn.execute("SELECT (SELECT COUNT(*) FROM files), (SELECT COUNT(*) FROM chunks)").fetchone()
        return {
            "mode": "incremental",
            "files": int(counts[0]),
            "chunks": int(counts[1]),
            "changed": len(changed),
            "removed": len(removed),
            "semanticMapChanged": semantic_map_changed,
            "fts5": use_fts,
            "elapsedSeconds": round(time.monotonic() - started, 6),
        }
    except ContextIndexError:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            conn = None
        raise
    except sqlite3.DatabaseError:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            conn = None
        return _rebuild(config, files, use_fts=use_fts, semantic_map=semantic_map)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def validate_context(config: ContextConfig) -> dict:
    try:
        semantic_map = load_semantic_map(config.root, config.semantic_map)
        files = discover_files(config)
    except SemanticMapError as exc:
        raise ContextIndexError(str(exc)) from exc
    identities: dict[str, str] = {}
    explicit = 0
    structural = 0
    mapped = 0
    for rel, info in files.items():
        if info.is_text:
            text = _read_text(config.root / rel)
            if text is not None:
                try:
                    chunks = chunk_text(rel, text, chunk_lines=config.chunk_lines, overlap_lines=config.overlap_lines)
                except ChunkError as exc:
                    raise ContextIndexError(str(exc)) from exc
                for chunk in chunks:
                    if chunk.anchor:
                        explicit += 1
                    if chunk.selector:
                        structural += 1
                    if chunk.chunk_id.startswith(("semantic:", "symbol:", "struct:", "heading:")):
                        previous = identities.get(chunk.chunk_id)
                        if previous and previous != rel:
                            raise ContextIndexError(f"duplicate semantic identity {chunk.chunk_id}: {previous}, {rel}")
                        identities[chunk.chunk_id] = rel
    for mapping in semantic_map.mappings:
        try:
            resolve_mapping(config.root, mapping)
        except SemanticMapError as exc:
            raise ContextIndexError(str(exc)) from exc
        identity = f"semantic:{mapping.semantic_id}"
        previous = identities.get(identity)
        if previous:
            raise ContextIndexError(f"duplicate semantic identity {mapping.semantic_id}: {previous}, {mapping.file}")
        identities[identity] = mapping.file
        mapped += 1
    return {
        "status": "pass",
        "files": len(files),
        "stableIdentities": len(identities),
        "explicitAnchors": explicit,
        "structuralSelectors": structural,
        "sidecarMappings": mapped,
    }


def related_paths(config: ContextConfig, paths: set[str], *, depth: int = 1) -> set[str]:
    if not paths or depth <= 0 or not config.database.is_file():
        return set(paths)
    conn = _connect(config.database)
    try:
        result = set(paths)
        frontier = set(paths)
        for _ in range(depth):
            if not frontier:
                break
            placeholders = ",".join("?" for _ in frontier)
            params = tuple(sorted(frontier))
            rows = conn.execute(
                f"SELECT source_path,target_path FROM file_relations WHERE source_path IN ({placeholders}) OR target_path IN ({placeholders})",
                (*params, *params),
            )
            discovered: set[str] = set()
            for row in rows:
                discovered.add(str(row["source_path"]))
                discovered.add(str(row["target_path"]))
            discovered -= result
            result.update(discovered)
            frontier = discovered
        return result
    finally:
        conn.close()


def index_stats(config: ContextConfig) -> dict:
    if not config.database.is_file():
        return {"exists": False, "database": str(config.database)}
    conn = _connect(config.database)
    try:
        meta = _db_meta(conn)
        files, chunks, relations = conn.execute(
            "SELECT (SELECT COUNT(*) FROM files), (SELECT COUNT(*) FROM chunks), (SELECT COUNT(*) FROM file_relations)"
        ).fetchone()
        priorities = {str(row[0]): int(row[1]) for row in conn.execute("SELECT priority, COUNT(*) FROM files GROUP BY priority")}
        return {
            "exists": True,
            "database": str(config.database),
            "schemaVersion": int(meta.get("schema_version", "0") or 0),
            "fts5": meta.get("fts5") == "1",
            "files": int(files),
            "chunks": int(chunks),
            "relations": int(relations),
            "priorities": priorities,
            "bytes": config.database.stat().st_size,
        }
    finally:
        conn.close()
