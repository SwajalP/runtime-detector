"""Repository indexing with incremental per-file updates.

Full indexing happens once at ``ledger init``. After that, only files whose
content hash changed are re-parsed (``sync_repository``) or the specific path
that an edit touched (``reindex_paths``). Every fact is keyed by
(commit, path, start, end, content_hash) so stale entries are detectable.
"""

from __future__ import annotations

from pathlib import Path

from ledger.config import LedgerConfig
from ledger.events.schema import content_hash, now_ms
from ledger.index.lexical import delete_fts_for_path, rebuild_fts
from ledger.index.parser import TREE_SITTER_AVAILABLE, parse_file
from ledger.index.resolve import resolve_callees
from ledger.index.symbols import upsert_regions


def iter_python_files(cfg: LedgerConfig) -> list[Path]:
    files = []
    for path in cfg.repo_root.rglob("*.py"):
        if cfg.is_excluded(path):
            continue
        if ".ledger" in path.parts:
            continue
        files.append(path)
    return sorted(files)


def _index_one(conn, cfg: LedgerConfig, path: Path, indexed_ms: int) -> tuple[int, int]:
    parsed = parse_file(path, cfg)
    rel = parsed.path
    delete_fts_for_path(conn, rel)
    conn.execute("DELETE FROM calls WHERE caller_id IN (SELECT region_id FROM source_regions WHERE path = ?)", (rel,))
    conn.execute("DELETE FROM imports WHERE region_id IN (SELECT region_id FROM source_regions WHERE path = ?)", (rel,))
    conn.execute("DELETE FROM source_regions WHERE path = ?", (rel,))
    upsert_regions(conn, parsed.regions, indexed_ms)
    rebuild_fts(conn, parsed.regions)
    for caller, callee in parsed.calls:
        conn.execute(
            "INSERT OR IGNORE INTO calls(caller_id, callee_name, callee_id) VALUES (?, ?, NULL)",
            (caller, callee),
        )
    for region_id, module, name in parsed.imports:
        conn.execute(
            "INSERT OR IGNORE INTO imports(region_id, module, name) VALUES (?, ?, ?)",
            (region_id, module, name),
        )
    return len(parsed.regions), len(parsed.calls)


def index_repository(conn, cfg: LedgerConfig) -> dict:
    """Full (re)index. Clears and rebuilds every table derived from source."""
    indexed_ms = now_ms()
    files = iter_python_files(cfg)
    n_regions = n_calls = 0
    conn.execute("DELETE FROM source_regions")
    conn.execute("DELETE FROM region_fts")
    conn.execute("DELETE FROM calls")
    conn.execute("DELETE FROM imports")
    for path in files:
        r, c = _index_one(conn, cfg, path, indexed_ms)
        n_regions += r
        n_calls += c
    _resolve_callees(conn)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('indexed_ms', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(indexed_ms),),
    )
    conn.commit()
    return {
        "files": len(files),
        "regions": n_regions,
        "calls": n_calls,
        "backend": "tree-sitter" if (TREE_SITTER_AVAILABLE and cfg.parser_backend != "ast") else "ast",
    }


def sync_repository(conn, cfg: LedgerConfig) -> dict:
    """Incremental: re-parse only files whose module hash differs from the index."""
    indexed_ms = now_ms()
    changed: list[str] = []
    seen: set[str] = set()
    known = {
        row["path"]: row["content_hash"]
        for row in conn.execute("SELECT path, content_hash FROM source_regions WHERE kind = 'module'")
    }
    for path in iter_python_files(cfg):
        rel = str(path.relative_to(cfg.repo_root))
        seen.add(rel)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if known.get(rel) != content_hash(text):
            _index_one(conn, cfg, path, indexed_ms)
            changed.append(rel)
    removed = [rel for rel in known if rel not in seen]
    for rel in removed:
        delete_fts_for_path(conn, rel)
        conn.execute("DELETE FROM source_regions WHERE path = ?", (rel,))
    if changed or removed:
        _resolve_callees(conn)
    conn.commit()
    return {"changed": changed, "removed": removed}


def reindex_paths(conn, cfg: LedgerConfig, rel_paths: list[str]) -> dict:
    indexed_ms = now_ms()
    n = 0
    for rel in rel_paths:
        path = cfg.repo_root / rel
        if not path.exists() or path.suffix != ".py":
            delete_fts_for_path(conn, rel)
            conn.execute("DELETE FROM source_regions WHERE path = ?", (rel,))
            continue
        r, _ = _index_one(conn, cfg, path, indexed_ms)
        n += r
    _resolve_callees(conn)
    conn.commit()
    return {"paths": rel_paths, "regions": n}


def _resolve_callees(conn) -> None:
    """Bind callee names using imports, ``self.attr`` types, and same-file defs."""
    resolve_callees(conn)
