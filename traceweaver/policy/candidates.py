"""Candidate generation: favour recall. Ranking and admission enforce precision.

Sources (spec §8.1):
  1. exact symbol matches            -> "symbol"
  2. FTS/BM25 lexical matches        -> "lexical" / "seed_lexical"
  3. callers / callees of actives    -> "caller" / "callee"
  4. regions executed by last test   -> "execution" (via working set)
  5. exception / diagnostic frames   -> "diagnostic" (via working set, pinned)
  6. historically co-accessed        -> "co_access" (this session + prior sessions on same repo)
  7. files changed in working tree   -> "diff"
  8. recently useful regions         -> "working_set"
"""

from __future__ import annotations

import sqlite3

from traceweaver.config import TraceWeaverConfig
from traceweaver.gitutil import git_dirty_files
from traceweaver.index.lexical import fts_search, like_search
from traceweaver.index.symbols import find_symbol, regions_for_path


def generate_candidates(
    conn: sqlite3.Connection,
    cfg: TraceWeaverConfig,
    session_id: str,
    objective: str,
    seed: str | None = None,
) -> list[dict]:
    seen: dict[str, dict] = {}

    def add(region: dict | None, source: str, weight: float = 1.0) -> None:
        if not region:
            return
        rid = region["region_id"]
        if rid not in seen:
            item = dict(region)
            item["candidate_sources"] = [source]
            item["candidate_weight"] = weight
            item["source_weights"] = {source: weight}
            seen[rid] = item
        else:
            if source not in seen[rid]["candidate_sources"]:
                seen[rid]["candidate_sources"].append(source)
            seen[rid]["candidate_weight"] += weight
            seen[rid]["source_weights"][source] = seen[rid]["source_weights"].get(source, 0) + weight

    def region(rid: str) -> dict | None:
        row = conn.execute("SELECT * FROM source_regions WHERE region_id = ?", (rid,)).fetchone()
        return dict(row) if row else None

    # 1-2. lexical + symbol
    lexical = fts_search(conn, objective, 20) or like_search(conn, objective, 20)
    for rank, row in enumerate(lexical):
        add(row, "lexical", max(0.4, 1.0 - rank * 0.04))
    if seed:
        for row in find_symbol(conn, seed):
            add(row, "symbol", 1.4 if row.get("symbol") == seed else 1.0)
        for row in fts_search(conn, seed, 10):
            add(row, "seed_lexical", 0.8)

    # 4-5, 8. working set (execution, diagnostics, recency)
    ws_rows = conn.execute(
        """
        SELECT * FROM working_set
        WHERE session_id = ? AND stale = 0
        ORDER BY execution_score DESC, access_frequency DESC
        LIMIT 60
        """,
        (session_id,),
    ).fetchall()
    for row in ws_rows:
        r = region(row["region_id"])
        if not r:
            continue
        add(r, "working_set", 0.6 + float(row["access_frequency"] or 0) * 0.1)
        if float(row["execution_score"] or 0) > 0.3:
            add(r, "execution", float(row["execution_score"]))
        if int(row["pinned"] or 0) and float(row["edit_likelihood"] or 0) > 0.5:
            add(r, "diagnostic", 1.0)

    # 3. structural neighbours of the most promising actives
    actives = sorted(seen.values(), key=lambda x: x["candidate_weight"], reverse=True)[:12]
    for item in actives:
        rid = item["region_id"]
        for call in conn.execute(
            "SELECT callee_id FROM calls WHERE caller_id = ? AND callee_id IS NOT NULL", (rid,)
        ):
            add(region(call["callee_id"]), "callee", 0.9)
        for call in conn.execute("SELECT caller_id FROM calls WHERE callee_id = ?", (rid,)):
            add(region(call["caller_id"]), "caller", 0.85)

    # 6. co-access: this session (strong) + prior sessions on this repo (weaker, L2)
    active_ids = [i["region_id"] for i in actives]
    if active_ids:
        marks = ",".join("?" * len(active_ids))
        for row in conn.execute(
            f"""
            SELECT region_a, region_b, weight, session_id FROM co_access
            WHERE (region_a IN ({marks}) OR region_b IN ({marks}))
            ORDER BY (session_id = ?) DESC, weight DESC
            LIMIT 40
            """,
            [*active_ids, *active_ids, session_id],
        ):
            other = row["region_b"] if row["region_a"] in active_ids else row["region_a"]
            scale = 1.0 if row["session_id"] == session_id else 0.45
            add(region(other), "co_access" if scale == 1.0 else "co_access_history", min(1.5, float(row["weight"]) * scale))

    # 7. dirty files
    for rel in git_dirty_files(cfg.repo_root)[:10]:
        for r in regions_for_path(conn, rel):
            if r["kind"] != "module":
                add(r, "diff", 0.7)

    return list(seen.values())


def neighbors(conn: sqlite3.Connection, region_id: str) -> list[str]:
    ids: list[str] = []
    for row in conn.execute(
        "SELECT callee_id FROM calls WHERE caller_id = ? AND callee_id IS NOT NULL", (region_id,)
    ):
        ids.append(row["callee_id"])
    for row in conn.execute("SELECT caller_id FROM calls WHERE callee_id = ?", (region_id,)):
        ids.append(row["caller_id"])
    for row in conn.execute(
        "SELECT region_a, region_b FROM co_access WHERE region_a = ? OR region_b = ? ORDER BY weight DESC LIMIT 6",
        (region_id, region_id),
    ):
        ids.append(row["region_b"] if row["region_a"] == region_id else row["region_a"])
    return list(dict.fromkeys(i for i in ids if i and i != region_id))
