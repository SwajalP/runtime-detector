"""Hash-based invalidation (spec §8.6).

On edit:
  1. find regions whose stored content hash no longer matches the bytes on disk
     at their recorded line range;
  2. mark them stale in every session's working set (never served as exact code);
  3. reparse the file incrementally;
  4. migrate working-set state to the successor region with the same symbol so
     history and co-access value survive the edit, with the new content version;
  5. emit an ``invalidate`` event with the old→new mapping for the UI.
"""

from __future__ import annotations

import json
import sqlite3

from traceweaver.config import TraceWeaverConfig
from traceweaver.events.schema import content_hash, now_ms
from traceweaver.index.incremental import reindex_paths
from traceweaver.index.symbols import find_by_symbol_in_path, regions_for_path


def stale_regions_for_path(conn: sqlite3.Connection, cfg: TraceWeaverConfig, rel_path: str) -> list[dict]:
    """Regions of ``rel_path`` whose content hash mismatches the current file."""
    full = cfg.repo_root / rel_path
    old = regions_for_path(conn, rel_path)
    if not full.exists():
        return old
    text = full.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    stale = []
    for region in old:
        if region["kind"] == "module":
            if region["content_hash"] != content_hash(text):
                stale.append(region)
            continue
        slice_ = "\n".join(lines[region["start_line"] - 1 : region["end_line"]])
        if content_hash(slice_) != region["content_hash"]:
            stale.append(region)
    return stale


def is_region_fresh(cfg: TraceWeaverConfig, region: dict) -> bool:
    """True iff the region's recorded hash matches disk right now."""
    full = cfg.repo_root / region["path"]
    if not full.exists():
        return False
    try:
        text = full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if region.get("kind") == "module":
        return content_hash(text) == region["content_hash"]
    lines = text.splitlines()
    slice_ = "\n".join(lines[region["start_line"] - 1 : region["end_line"]])
    return content_hash(slice_) == region["content_hash"]


def invalidate_path(
    conn: sqlite3.Connection,
    cfg: TraceWeaverConfig,
    session_id: str,
    rel_path: str,
    collector=None,
) -> list[str]:
    stale = stale_regions_for_path(conn, cfg, rel_path)
    stale_ids = [r["region_id"] for r in stale]
    if not stale_ids:
        return []

    marks = ",".join("?" * len(stale_ids))
    # Mark stale in *every* session: the content is gone for everyone.
    conn.execute(
        f"UPDATE working_set SET stale = 1, staleness = 1.0, admitted = 0 WHERE region_id IN ({marks})",
        stale_ids,
    )

    reindex_paths(conn, cfg, [rel_path])

    mapping: dict[str, str | None] = {}
    for region in stale:
        successor = find_by_symbol_in_path(conn, rel_path, region["symbol"]) if region.get("symbol") else None
        mapping[region["region_id"]] = successor["region_id"] if successor else None
        if successor:
            _migrate_state(conn, region["region_id"], successor)

    for rid, new_id in mapping.items():
        conn.execute(
            "INSERT INTO controller_log(session_id, timestamp_ms, action, region_id, detail_json) VALUES (?, ?, 'invalidate', ?, ?)",
            (session_id, now_ms(), rid, json.dumps({"replaced_by": new_id})),
        )
    conn.commit()

    if collector:
        collector.record(
            session_id=session_id,
            source="controller",
            operation="invalidate",
            query=rel_path,
            regions=stale_ids,
            extra={
                "reason": "content_hash_mismatch",
                "replaced_by": mapping,
                "symbols": [r.get("symbol") for r in stale],
            },
        )
    return stale_ids


def _migrate_state(conn: sqlite3.Connection, old_id: str, successor: dict) -> None:
    new_id = successor["region_id"]
    rows = conn.execute("SELECT * FROM working_set WHERE region_id = ?", (old_id,)).fetchall()
    for row in rows:
        row = dict(row)
        conn.execute(
            """
            INSERT INTO working_set (
              session_id, region_id, last_access_turn, access_frequency, agent_access_score,
              execution_score, intent_score, structural_score, co_access_score, edit_likelihood,
              staleness, token_cost, observed_future_use, pinned, admitted, stale, explanation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 0, 0, ?)
            ON CONFLICT(session_id, region_id) DO UPDATE SET
              access_frequency = MAX(working_set.access_frequency, excluded.access_frequency),
              execution_score = MAX(working_set.execution_score, excluded.execution_score),
              agent_access_score = MAX(working_set.agent_access_score, excluded.agent_access_score),
              co_access_score = MAX(working_set.co_access_score, excluded.co_access_score),
              edit_likelihood = MAX(working_set.edit_likelihood, excluded.edit_likelihood),
              pinned = MAX(working_set.pinned, excluded.pinned)
            """,
            (
                row["session_id"], new_id, row["last_access_turn"], row["access_frequency"],
                row["agent_access_score"], row["execution_score"] * 0.8, row["intent_score"],
                row["structural_score"], row["co_access_score"], max(row["edit_likelihood"], 0.7),
                successor["token_count"], row["observed_future_use"], row["pinned"],
                "reparsed after edit (new content version)",
            ),
        )
        conn.execute(
            "UPDATE working_set SET replaced_by = ? WHERE session_id = ? AND region_id = ?",
            (new_id, row["session_id"], old_id),
        )
    # Retain co-access edges, re-pointed at the new content version.
    conn.execute("UPDATE OR IGNORE co_access SET region_a = ? WHERE region_a = ?", (new_id, old_id))
    conn.execute("UPDATE OR IGNORE co_access SET region_b = ? WHERE region_b = ?", (new_id, old_id))
