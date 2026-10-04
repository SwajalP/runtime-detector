"""Lexical retrieval over source regions: SQLite FTS5 with a LIKE fallback."""

from __future__ import annotations

import re
import sqlite3

_STOP = {
    "the", "and", "when", "with", "from", "that", "this", "into", "for", "find",
    "locate", "which", "where", "used", "after", "its", "are", "was", "has",
}


def tokenize(query: str) -> list[str]:
    out = []
    for raw in re.split(r"[^A-Za-z0-9_]+", query or ""):
        if not raw:
            continue
        # split snake_case and CamelCase into sub-terms as well as the full term
        parts = [raw]
        if "_" in raw:
            parts.extend(p for p in raw.split("_") if p)
        parts.extend(re.findall(r"[A-Z][a-z0-9]+|[a-z0-9]+|[A-Z]+(?![a-z])", raw))
        for p in parts:
            p = p.lower()
            if len(p) > 2 and p not in _STOP and p not in out:
                out.append(p)
    return out


def fts_search(conn: sqlite3.Connection, query: str, limit: int = 25) -> list[dict]:
    q = _fts_query(query)
    if not q:
        return []
    try:
        rows = conn.execute(
            """
            SELECT r.*, bm25(region_fts, 0.0, 2.0, 6.0, 3.0, 1.0) AS rank
            FROM region_fts
            JOIN source_regions r ON r.region_id = region_fts.region_id
            WHERE region_fts MATCH ?
              AND r.kind != 'module'
            ORDER BY rank
            LIMIT ?
            """,
            (q, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return like_search(conn, query, limit)


def like_search(conn: sqlite3.Connection, query: str, limit: int = 25) -> list[dict]:
    tokens = tokenize(query)
    if not tokens:
        return []
    clauses = []
    params: list = []
    for t in tokens[:6]:
        clauses.append("(lower(COALESCE(symbol,'')) LIKE ? OR lower(COALESCE(body,'')) LIKE ? OR lower(path) LIKE ?)")
        params.extend([f"%{t}%", f"%{t}%", f"%{t}%"])
    sql = f"""
        SELECT * FROM source_regions
        WHERE kind != 'module' AND ({' OR '.join(clauses)})
        ORDER BY (end_line - start_line)
        LIMIT ?
    """
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def grep_regions(conn: sqlite3.Connection, pattern: str, limit: int = 40) -> list[dict]:
    """Exact regex grep over indexed bodies; mirrors what an agent's Grep would hit."""
    try:
        rx = re.compile(pattern, re.I)
    except re.error:
        rx = re.compile(re.escape(pattern), re.I)
    out = []
    for row in conn.execute("SELECT * FROM source_regions WHERE kind != 'module'"):
        if rx.search(row["body"] or ""):
            out.append(dict(row))
            if len(out) >= limit:
                break
    return out


def rebuild_fts(conn: sqlite3.Connection, regions) -> None:
    for r in regions:
        conn.execute("DELETE FROM region_fts WHERE region_id = ?", (r.region_id,))
        conn.execute(
            "INSERT INTO region_fts(region_id, path, symbol, signature, body) VALUES (?, ?, ?, ?, ?)",
            (r.region_id, r.path, _expand(r.symbol or ""), r.signature or "", r.body or ""),
        )


def delete_fts_for_path(conn: sqlite3.Connection, rel_path: str) -> None:
    conn.execute(
        "DELETE FROM region_fts WHERE region_id IN (SELECT region_id FROM source_regions WHERE path = ?)",
        (rel_path,),
    )


def _expand(symbol: str) -> str:
    """Store `Class.method` as `Class.method Class method` so sub-terms match."""
    return " ".join([symbol, *tokenize(symbol)])


def _fts_query(query: str) -> str:
    tokens = tokenize(query)
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens[:10])
