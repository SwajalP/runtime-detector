"""Call-graph views over the LEDGER index, and bundle explanation tags."""

from __future__ import annotations

import sqlite3

from ledger.graphalg.algorithms import reverse_bfs, stoer_wagner, tarjan_scc

ALGORITHMS = ("reverse_bfs", "min_cut", "tarjan")


def call_graph(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Directed caller → callee graph. Every non-module region is a node."""
    graph: dict[str, list[str]] = {}
    for row in conn.execute("SELECT region_id FROM source_regions WHERE kind != 'module'"):
        graph[row["region_id"]] = []
    for row in conn.execute(
        "SELECT caller_id, callee_id FROM calls WHERE callee_id IS NOT NULL"
    ):
        src, dst = row["caller_id"], row["callee_id"]
        graph.setdefault(src, [])
        graph.setdefault(dst, [])
        if dst not in graph[src]:
            graph[src].append(dst)
    for node in graph:
        graph[node] = sorted(graph[node])
    return graph


def _labels(conn: sqlite3.Connection, ids: list[str], limit: int = 40) -> list[str]:
    out: list[str] = []
    for rid in ids[:limit]:
        row = conn.execute(
            "SELECT path, symbol FROM source_regions WHERE region_id = ?", (rid,)
        ).fetchone()
        if row and row["symbol"]:
            out.append(f"{row['path']}::{row['symbol']}")
        elif row:
            out.append(str(row["path"]))
        else:
            out.append(rid)
    return out


def resolve_region(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    path_substr: str | None = None,
) -> sqlite3.Row | None:
    symbol = (symbol or "").strip()
    if not symbol:
        return None
    rows = conn.execute(
        """
        SELECT region_id, symbol, path, kind FROM source_regions
        WHERE kind != 'module' AND (symbol = ? OR symbol LIKE ?)
        ORDER BY (symbol = ?) DESC, (path LIKE ?) DESC, path
        """,
        (symbol, f"%.{symbol}", symbol, f"%{path_substr or ''}%"),
    ).fetchall()
    if path_substr:
        for row in rows:
            if path_substr in (row["path"] or ""):
                return row
    return rows[0] if rows else None


def undirected_component(
    graph: dict[str, list[str]],
    seed: str,
    extra: set[str] | None = None,
) -> dict[str, dict[str, float]]:
    """Undirected unit-weight graph of the seed's neighborhood, seed component only."""
    nodes = set(extra or ())
    nodes.add(seed)
    for node in list(nodes):
        for dst in graph.get(node, []):
            nodes.add(dst)
    # also pull direct predecessors already in `nodes` via the directed edges
    adj: dict[str, dict[str, float]] = {n: {} for n in nodes}
    for src, dsts in graph.items():
        for dst in dsts:
            if src in nodes and dst in nodes and src != dst:
                adj.setdefault(src, {})
                adj.setdefault(dst, {})
                adj[src][dst] = adj[src].get(dst, 0.0) + 1.0
                adj[dst][src] = adj[dst].get(src, 0.0) + 1.0
    seen: set[str] = set()
    stack = [seed] if seed in adj else []
    while stack:
        u = stack.pop()
        if u in seen:
            continue
        seen.add(u)
        stack.extend(adj.get(u, {}))
    return {n: {v: w for v, w in adj.get(n, {}).items() if v in seen} for n in seen}


def symbol_neighborhood(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    path_substr: str | None = None,
) -> dict:
    """Caller cone (reverse BFS) plus direct callees. Excludes the seed itself."""
    row = resolve_region(conn, symbol, path_substr=path_substr)
    if row is None:
        return {"seed": None, "neighborhood": [], "labels": []}
    graph = call_graph(conn)
    seed = row["region_id"]
    if seed not in graph:
        graph[seed] = []
    callers = [n for n in reverse_bfs(graph, seed) if n != seed]
    callees = [n for n in graph.get(seed, []) if n != seed]
    neighborhood = list(dict.fromkeys([*callers, *callees]))
    return {
        "seed": f"{row['path']}::{row['symbol']}",
        "seed_region_id": seed,
        "neighborhood": neighborhood,
        "labels": _labels(conn, neighborhood),
    }


def annotate_entries(
    conn: sqlite3.Connection,
    entries: list[dict],
    *,
    seed: str | None = None,
    objective: str = "",
) -> dict:
    """Stamp reverse_bfs / min_cut / tarjan onto bundle entries. Does not re-rank."""
    graph = call_graph(conn)
    seed_row = resolve_region(conn, seed or "", path_substr="discount_policy") if seed else None
    if seed_row is None and seed:
        seed_row = resolve_region(conn, seed)
    if seed_row is None and entries:
        best = max(entries, key=lambda e: float(e.get("score") or 0))
        seed_row = conn.execute(
            "SELECT region_id, symbol, path, kind FROM source_regions WHERE region_id = ?",
            (best["region_id"],),
        ).fetchone()
    report: dict = {
        "algorithms": list(ALGORITHMS),
        "objective": objective,
        "seed_region_id": None,
        "seed_symbol": None,
        "seed_path": None,
    }
    if seed_row is None:
        report["note"] = "no seed region in the index"
        for entry in entries:
            entry["graph_tags"] = []
        return report

    seed_id = seed_row["region_id"]
    if seed_id not in graph:
        graph[seed_id] = []
    report["seed_region_id"] = seed_id
    report["seed_symbol"] = seed_row["symbol"]
    report["seed_path"] = seed_row["path"]

    order = reverse_bfs(graph, seed_id)
    neighborhood = [n for n in order if n != seed_id]
    report["reverse_bfs"] = {
        "order": order[:80],
        "neighborhood": neighborhood[:80],
        "labels": _labels(conn, neighborhood),
    }

    sccs = tarjan_scc(graph)
    seed_scc = next((comp for comp in sccs if seed_id in comp), [seed_id])
    large = [comp for comp in sccs if len(comp) >= 2]
    report["tarjan"] = {
        "seed_scc": seed_scc[:80],
        "seed_scc_size": len(seed_scc),
        "seed_scc_labels": _labels(conn, seed_scc),
        "large_scc_count": len(large),
        "largest_scc_size": max((len(comp) for comp in sccs), default=0),
    }

    neigh_ids = set(order)
    neigh_ids.update(graph.get(seed_id, []))
    adj = undirected_component(graph, seed_id, neigh_ids)
    cut = stoer_wagner(adj)
    left, right = set(cut["left"]), set(cut["right"])
    if seed_id in right and seed_id not in left:
        left, right = right, left
    elif seed_id not in left:
        left.add(seed_id)
    report["min_cut"] = {
        "algorithm": "stoer-wagner",
        "weight": cut.get("weight"),
        "seed_side": sorted(left)[:80],
        "other_side": sorted(right)[:80],
        "seed_side_labels": _labels(conn, sorted(left)),
        "other_side_labels": _labels(conn, sorted(right)),
    }

    bfs_set = set(order)
    scc_set = set(seed_scc)
    for entry in entries:
        rid = entry.get("region_id")
        tags: list[str] = []
        if rid in bfs_set:
            tags.append("reverse_bfs")
        if rid in left:
            tags.append("min_cut")
        if rid in scc_set:
            tags.append("tarjan")
        entry["graph_tags"] = tags
        if tags:
            sources = list(entry.get("sources") or [])
            for tag in tags:
                if tag not in sources:
                    sources.append(tag)
            entry["sources"] = sources
            suffix = "graph: " + ", ".join(tags)
            why = entry.get("why") or ""
            if suffix not in why:
                entry["why"] = f"{why}; {suffix}" if why else suffix
    return report
