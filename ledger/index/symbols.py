from __future__ import annotations

import sqlite3

from ledger.events.schema import SourceRegion


def upsert_regions(conn: sqlite3.Connection, regions: list[SourceRegion], indexed_ms: int) -> None:
    for r in regions:
        conn.execute(
            """
            INSERT INTO source_regions (
              region_id, repo_id, commit_sha, path, symbol, kind, start_line, end_line,
              content_hash, token_count, signature, body, indexed_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(region_id) DO UPDATE SET
              token_count=excluded.token_count,
              signature=excluded.signature,
              body=excluded.body,
              indexed_ms=excluded.indexed_ms
            """,
            (
                r.region_id,
                r.repo_id,
                r.commit_sha,
                r.path,
                r.symbol,
                r.kind,
                r.start_line,
                r.end_line,
                r.content_hash,
                r.token_count,
                r.signature,
                r.body,
                indexed_ms,
            ),
        )


def get_region(conn: sqlite3.Connection, region_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM source_regions WHERE region_id = ?", (region_id,)).fetchone()
    return dict(row) if row else None


def regions_for_path(conn: sqlite3.Connection, path: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM source_regions WHERE path = ? ORDER BY start_line",
        (path,),
    ).fetchall()
    return [dict(r) for r in rows]


def region_covering(conn: sqlite3.Connection, path: str, line: int) -> dict | None:
    row = conn.execute(
        """
        SELECT * FROM source_regions
        WHERE path = ? AND start_line <= ? AND end_line >= ?
          AND kind IN ('function', 'method', 'class')
        ORDER BY (end_line - start_line) ASC
        LIMIT 1
        """,
        (path, line, line),
    ).fetchone()
    if row:
        return dict(row)
    row = conn.execute(
        """
        SELECT * FROM source_regions
        WHERE path = ? AND kind = 'module'
        LIMIT 1
        """,
        (path,),
    ).fetchone()
    return dict(row) if row else None


def find_symbol(conn: sqlite3.Connection, name: str) -> list[dict]:
    name = (name or "").strip()
    if not name:
        return []
    rows = conn.execute(
        """
        SELECT * FROM source_regions
        WHERE symbol = ? OR symbol LIKE ?
        ORDER BY symbol = ? DESC, kind = 'module', start_line
        LIMIT 40
        """,
        (name, f"%{name}%", name),
    ).fetchall()
    return [dict(r) for r in rows]


def regions_in_range(conn: sqlite3.Connection, path: str, start: int, end: int) -> list[dict]:
    """Regions overlapping [start, end] in a file (used by edit/read range mapping)."""
    rows = conn.execute(
        """
        SELECT * FROM source_regions
        WHERE path = ? AND kind != 'module' AND start_line <= ? AND end_line >= ?
        ORDER BY start_line
        """,
        (path, end, start),
    ).fetchall()
    return [dict(r) for r in rows]


def find_by_symbol_in_path(conn: sqlite3.Connection, path: str, symbol: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM source_regions WHERE path = ? AND symbol = ? LIMIT 1",
        (path, symbol),
    ).fetchone()
    return dict(row) if row else None
