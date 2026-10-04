"""Judge-facing visual payloads: the code knowledge graph and the memory hierarchy.

Two pure functions over the TraceWeaver SQLite store plus a FastAPI router that
exposes them under ``/api/graph``.

* :func:`graph_payload` — source regions as nodes (grouped by file / package),
  joined with per-session working-set state and the latest bundle, with
  ``call`` / ``co_access`` / ``co_access_history`` / ``exec`` / ``successor``
  edges, pruned to a readable size.
* :func:`hierarchy_payload` — the "memory allocator" view: residents and
  occupancy of the logical cache levels L0 (active anchors), L1 (session
  bundle, bounded by ``cfg.token_budget``), L2 (repo-local cross-session
  memory) and the backing index, plus a recent controller activity log and
  honest fragmentation / pollution statistics.

Both are read-only and cheap (a handful of indexed queries).
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Request

LEVELS = ("L0", "L1", "L2", "backing")
LEVEL_LABELS = {
    "L0": "L0 · Active anchors",
    "L1": "L1 · Session bundle",
    "L2": "L2 · Repo-local memory",
    "backing": "Backing · Source index",
}
LOG_OPS = ("admit", "evict", "prefetch", "invalidate", "hit", "miss", "stale_blocked", "bundle_served")
AGENT_READ_OPS = ("read", "grep", "glob", "definition", "references")
COMPRESSED_LEVELS = ("signature", "summary")


# --------------------------------------------------------------------- helpers


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _loads(text: str | None, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def _package(path: str) -> str:
    parts = (path or "").split("/")
    return "/".join(parts[:-1]) if len(parts) > 1 else "."


def _resolve_session(runtime, session_id: str | None) -> str | None:
    if session_id:
        return session_id
    try:
        return runtime.latest_session()
    except Exception:
        return None


def _session_row(conn: sqlite3.Connection, session_id: str | None) -> dict:
    if not session_id:
        return {}
    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    return dict(row) if row else {}


def _latest_bundle(conn: sqlite3.Connection, session_id: str | None) -> dict | None:
    if not session_id:
        return None
    row = conn.execute(
        "SELECT payload_json FROM bundles WHERE session_id = ? ORDER BY created_ms DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return _loads(row["payload_json"], None) if row else None


def _events(conn: sqlite3.Connection, session_id: str | None, limit: int = 1500) -> list[dict]:
    """Session events, oldest first, with regions/extra decoded."""
    if not session_id:
        return []
    rows = _rows(
        conn,
        "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp_ms DESC, rowid DESC LIMIT ?",
        (session_id, limit),
    )
    rows.reverse()
    for e in rows:
        e["regions"] = _loads(e.pop("regions_json", None), [])
        e["extra"] = _loads(e.pop("extra_json", None), {})
    return rows


def _latest_trace(events: list[dict]) -> dict | None:
    for e in reversed(events):
        if e["source"] == "program_trace" or e["operation"] == "test":
            if e["regions"]:
                return e
    return None


def _symbol_hints(events: list[dict], bundle: dict | None) -> dict[str, dict]:
    """Best-effort symbol/path for regions that vanished from the index."""
    hints: dict[str, dict] = {}
    if bundle:
        for key in ("entries", "prefetch", "evicted", "rejected", "stale_blocked"):
            for e in bundle.get(key) or []:
                rid = e.get("region_id")
                if rid and rid not in hints:
                    hints[rid] = {"symbol": e.get("symbol"), "path": e.get("path")}
    for e in events:
        extra = e.get("extra") or {}
        regions = e.get("regions") or []
        if e["operation"] == "invalidate":
            for rid, sym in zip(regions, extra.get("symbols") or []):
                hints.setdefault(rid, {"symbol": sym, "path": e.get("query")})
            for old, new in (extra.get("replaced_by") or {}).items():
                if new and new not in hints and old in hints:
                    hints[new] = {"symbol": hints[old].get("symbol"), "path": e.get("query")}
        elif extra.get("symbol") and len(regions) == 1:
            hints.setdefault(regions[0], {"symbol": extra["symbol"], "path": None})
    return hints


def _level_for(ws: dict, in_bundle: bool, in_frames: bool, in_other_sessions: bool) -> str:
    stale = bool(ws.get("stale"))
    pinned = bool(ws.get("pinned"))
    admitted = bool(ws.get("admitted"))
    if stale:
        # Stale lines keep the slot they occupied so the UI can draw them red
        # with an arrow to the successor; they are never served.
        if in_bundle:
            return "L1"
        if pinned:
            return "L0"
        return "L2" if ws else "backing"
    if pinned or in_frames:
        return "L0"
    if admitted or in_bundle:
        return "L1"
    if ws or in_other_sessions:
        return "L2"
    return "backing"


# -------------------------------------------------------------- graph payload


def graph_payload(runtime, session_id: str | None = None, max_nodes: int = 160) -> dict[str, Any]:
    conn: sqlite3.Connection = runtime.conn
    sid = _resolve_session(runtime, session_id)
    session = _session_row(conn, sid)
    repo_id = session.get("repo_id")

    regions = _rows(
        conn,
        "SELECT region_id, path, symbol, kind, start_line, end_line, token_count, content_hash FROM source_regions",
    )
    by_id: dict[str, dict] = {r["region_id"]: r for r in regions}
    non_module = [r for r in regions if r["kind"] != "module"]
    candidates = non_module if non_module else regions
    cand_ids = {r["region_id"] for r in candidates}

    ws_rows = _rows(conn, "SELECT * FROM working_set WHERE session_id = ?", (sid,)) if sid else []
    ws: dict[str, dict] = {w["region_id"]: w for w in ws_rows}

    bundle = _latest_bundle(conn, sid)
    bundle_entries: dict[str, dict] = {}
    prefetch_ids: set[str] = set()
    if bundle:
        for e in bundle.get("entries") or []:
            bundle_entries[e["region_id"]] = e
        prefetch_ids = {p["region_id"] for p in bundle.get("prefetch") or []}

    events = _events(conn, sid)
    trace = _latest_trace(events)
    exec_path: list[str] = list(dict.fromkeys(trace["regions"])) if trace else []
    frame_ids: set[str] = set()
    if trace:
        for fr in (trace.get("extra") or {}).get("frames") or []:
            if fr.get("region_id"):
                frame_ids.add(fr["region_id"])
    agent_read: set[str] = set()
    for e in events:
        if e["source"] == "agent_tool" and e["operation"] in AGENT_READ_OPS:
            agent_read.update(e["regions"])
    for rid, w in ws.items():
        if float(w.get("agent_access_score") or 0) > 0.2:
            agent_read.add(rid)

    # Cross-session memory on the same repo (L2).
    other_sessions: list[str] = []
    if repo_id:
        other_sessions = [
            r["session_id"]
            for r in _rows(
                conn,
                "SELECT session_id FROM sessions WHERE repo_id = ? AND session_id != ? ORDER BY started_ms DESC LIMIT 12",
                (repo_id, sid or ""),
            )
        ]
    other_ws_ids: set[str] = set()
    if other_sessions:
        marks = ",".join("?" * len(other_sessions))
        other_ws_ids = {
            r["region_id"]
            for r in _rows(conn, f"SELECT DISTINCT region_id FROM working_set WHERE session_id IN ({marks})", tuple(other_sessions))
        }

    hints = _symbol_hints(events, bundle)

    # Working-set entries whose region vanished from the index are kept as
    # ghost nodes so stale → successor arrows still render.
    def ghost(rid: str) -> dict:
        h = hints.get(rid, {})
        w = ws.get(rid, {})
        succ = by_id.get(w.get("replaced_by") or "", {})
        return {
            "region_id": rid,
            "path": h.get("path") or succ.get("path") or "(removed)",
            "symbol": h.get("symbol") or succ.get("symbol") or "(invalidated region)",
            "kind": succ.get("kind") or "function",
            "start_line": succ.get("start_line") or 0,
            "end_line": succ.get("end_line") or 0,
            "token_count": int(w.get("token_cost") or succ.get("token_count") or 1),
            "content_hash": None,
            "ghost": True,
        }

    for rid in list(ws) + list(bundle_entries):
        if rid not in by_id:
            by_id[rid] = ghost(rid)
            cand_ids.add(rid)

    # ---- edges over the full candidate set (needed for degree-based pruning)
    edges: list[dict] = []
    for r in _rows(conn, "SELECT caller_id, callee_id FROM calls WHERE callee_id IS NOT NULL"):
        a, b = r["caller_id"], r["callee_id"]
        if a in cand_ids and b in cand_ids and a != b:
            edges.append({"source": a, "target": b, "type": "call", "weight": 1.0})

    if sid:
        co = _rows(
            conn,
            "SELECT region_a, region_b, weight FROM co_access WHERE session_id = ? ORDER BY weight DESC LIMIT 600",
            (sid,),
        )
        per_node: dict[str, int] = defaultdict(int)
        for r in co:
            a, b = r["region_a"], r["region_b"]
            if a not in cand_ids or b not in cand_ids or a == b:
                continue
            if per_node[a] >= 4 and per_node[b] >= 4:
                continue
            per_node[a] += 1
            per_node[b] += 1
            edges.append({"source": a, "target": b, "type": "co_access", "weight": round(float(r["weight"]), 3)})

    if other_sessions:
        marks = ",".join("?" * len(other_sessions))
        hist = _rows(
            conn,
            f"SELECT region_a, region_b, MAX(weight) AS weight FROM co_access WHERE session_id IN ({marks}) "
            "GROUP BY region_a, region_b ORDER BY weight DESC LIMIT 300",
            tuple(other_sessions),
        )
        seen_pairs = {(e["source"], e["target"]) for e in edges if e["type"] == "co_access"}
        per_node_h: dict[str, int] = defaultdict(int)
        for r in hist:
            a, b = r["region_a"], r["region_b"]
            if a not in cand_ids or b not in cand_ids or a == b or (a, b) in seen_pairs:
                continue
            if per_node_h[a] >= 2 and per_node_h[b] >= 2:
                continue
            per_node_h[a] += 1
            per_node_h[b] += 1
            edges.append({"source": a, "target": b, "type": "co_access_history", "weight": round(float(r["weight"]) * 0.5, 3)})

    path_ids = [rid for rid in exec_path if rid in cand_ids]
    for i, (a, b) in enumerate(zip(path_ids, path_ids[1:])):
        edges.append({"source": a, "target": b, "type": "exec", "weight": 1.0, "order": i})

    for rid, w in ws.items():
        succ = w.get("replaced_by")
        if w.get("stale") and succ:
            if succ not in by_id:
                by_id[succ] = ghost(succ)
                cand_ids.add(succ)
            edges.append({"source": rid, "target": succ, "type": "successor", "weight": 1.0})

    # ---- prune
    degree: dict[str, int] = defaultdict(int)
    for e in edges:
        degree[e["source"]] += 1
        degree[e["target"]] += 1

    core: set[str] = set(ws) | set(bundle_entries) | prefetch_ids | set(path_ids)
    core &= cand_ids
    keep: list[str] = sorted(core, key=lambda r: -degree[r])
    kept: set[str] = set(keep)
    if len(kept) < max_nodes:
        hop: set[str] = set()
        for e in edges:
            if e["source"] in core and e["target"] not in kept:
                hop.add(e["target"])
            elif e["target"] in core and e["source"] not in kept:
                hop.add(e["source"])
        for rid in sorted(hop, key=lambda r: -degree[r]):
            if len(kept) >= max_nodes:
                break
            kept.add(rid)
            keep.append(rid)
    if len(kept) < max_nodes:
        rest = sorted((r for r in cand_ids if r not in kept), key=lambda r: (-degree[r], by_id[r]["path"], by_id[r]["start_line"]))
        for rid in rest:
            if len(kept) >= max_nodes:
                break
            kept.add(rid)
            keep.append(rid)
    truncated_nodes = len(cand_ids) - len(kept)
    kept_edges = [e for e in edges if e["source"] in kept and e["target"] in kept]
    truncated_edges = len(edges) - len(kept_edges)

    # ---- nodes
    exec_index = {rid: i for i, rid in enumerate(path_ids)}
    nodes: list[dict] = []
    files: dict[str, dict] = {}
    for rid in keep:
        r = by_id[rid]
        w = ws.get(rid, {})
        be = bundle_entries.get(rid)
        in_bundle = be is not None
        level = _level_for(w, in_bundle, rid in frame_ids, rid in other_ws_ids)
        executed = rid in exec_index or float(w.get("execution_score") or 0) > 0.2
        read = rid in agent_read
        node = {
            "id": rid,
            "symbol": r.get("symbol") or r.get("path"),
            "path": r.get("path"),
            "package": _package(r.get("path") or ""),
            "kind": r.get("kind"),
            "token_count": int(r.get("token_count") or 0),
            "start_line": r.get("start_line"),
            "end_line": r.get("end_line"),
            "ghost": bool(r.get("ghost")),
            "level": level,
            "in_bundle": in_bundle,
            "prefetched": rid in prefetch_ids,
            "in_working_set": bool(w),
            "executed": executed,
            "agent_read": read,
            "joined": executed and read,
            "frame": rid in frame_ids,
            "exec_order": exec_index.get(rid),
            "execution_score": round(float(w.get("execution_score") or 0), 3),
            "agent_access_score": round(float(w.get("agent_access_score") or 0), 3),
            "co_access_score": round(float(w.get("co_access_score") or 0), 3),
            "edit_likelihood": round(float(w.get("edit_likelihood") or 0), 3),
            "access_frequency": round(float(w.get("access_frequency") or 0), 2),
            "last_access_turn": w.get("last_access_turn"),
            "admitted": bool(w.get("admitted")),
            "pinned": bool(w.get("pinned")),
            "stale": bool(w.get("stale")),
            "replaced_by": w.get("replaced_by"),
            "explanation": (be or {}).get("why") or w.get("explanation"),
            "score": (be or {}).get("score"),
            "repr": (be or {}).get("level"),
            "served_tokens": (be or {}).get("token_count"),
            "degree": degree[rid],
        }
        nodes.append(node)
        f = files.setdefault(
            node["path"],
            {"path": node["path"], "package": node["package"], "tokens": 0, "regions": 0, "heat": 0.0, "levels": {lv: 0 for lv in LEVELS}, "stale": 0},
        )
        f["tokens"] += node["token_count"]
        f["regions"] += 1
        f["heat"] += node["execution_score"] + 0.15 * node["access_frequency"]
        f["levels"][level] += 1
        f["stale"] += 1 if node["stale"] else 0
    for f in files.values():
        f["heat"] = round(f["heat"], 3)

    nodes.sort(key=lambda n: (n["package"], n["path"], n["start_line"] or 0))
    return {
        "session_id": sid,
        "repo_id": repo_id,
        "condition": session.get("condition"),
        "task_id": session.get("task_id"),
        "bundle_id": (bundle or {}).get("bundle_id"),
        "objective": (bundle or {}).get("objective") or session.get("objective"),
        "token_budget": int(getattr(runtime.cfg, "token_budget", 0) or 0),
        "nodes": nodes,
        "edges": kept_edges,
        "exec_path": path_ids,
        "frames": sorted(frame_ids & kept),
        "files": sorted(files.values(), key=lambda f: (-f["heat"], f["path"])),
        "counts": {
            "nodes": len(nodes),
            "edges": len(kept_edges),
            "regions_total": len(regions),
            "by_type": _count_by(kept_edges, "type"),
            "by_level": _count_by(nodes, "level"),
            "joined": sum(1 for n in nodes if n["joined"]),
            "stale": sum(1 for n in nodes if n["stale"]),
        },
        "truncated": {"nodes": max(0, truncated_nodes), "edges": max(0, truncated_edges)},
        "other_sessions": len(other_sessions),
    }


def _count_by(items: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for it in items:
        out[str(it.get(key))] += 1
    return dict(out)


# ---------------------------------------------------------- hierarchy payload


def hierarchy_payload(runtime, session_id: str | None = None) -> dict[str, Any]:
    conn: sqlite3.Connection = runtime.conn
    sid = _resolve_session(runtime, session_id)
    session = _session_row(conn, sid)
    repo_id = session.get("repo_id")
    budget = int(getattr(runtime.cfg, "token_budget", 0) or 0)

    totals = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(token_count), 0) AS t FROM source_regions").fetchone()
    by_kind = {r["kind"]: r["n"] for r in _rows(conn, "SELECT kind, COUNT(*) AS n FROM source_regions GROUP BY kind")}
    n_files = conn.execute("SELECT COUNT(DISTINCT path) AS n FROM source_regions").fetchone()["n"]

    ws_rows = _rows(conn, "SELECT * FROM working_set WHERE session_id = ?", (sid,)) if sid else []
    ws: dict[str, dict] = {w["region_id"]: w for w in ws_rows}
    region_ids = set(ws)

    bundle = _latest_bundle(conn, sid)
    entries: dict[str, dict] = {}
    prefetch: dict[str, dict] = {}
    if bundle:
        entries = {e["region_id"]: e for e in bundle.get("entries") or []}
        prefetch = {p["region_id"]: p for p in bundle.get("prefetch") or []}
    region_ids |= set(entries) | set(prefetch)

    events = _events(conn, sid)
    trace = _latest_trace(events)
    frame_ids: set[str] = set()
    if trace:
        for fr in (trace.get("extra") or {}).get("frames") or []:
            if fr.get("region_id"):
                frame_ids.add(fr["region_id"])
    region_ids |= frame_ids

    other_sessions: list[dict] = []
    other_ws: dict[str, dict] = {}
    if repo_id:
        other_sessions = _rows(
            conn,
            "SELECT session_id, task_id, condition, started_ms FROM sessions WHERE repo_id = ? AND session_id != ? ORDER BY started_ms DESC LIMIT 12",
            (repo_id, sid or ""),
        )
    if other_sessions:
        marks = ",".join("?" * len(other_sessions))
        for r in _rows(
            conn,
            f"""
            SELECT region_id, COUNT(*) AS sessions, MAX(execution_score) AS execution_score,
                   MAX(agent_access_score) AS agent_access_score, SUM(access_frequency) AS access_frequency,
                   MAX(last_access_turn) AS last_access_turn
            FROM working_set WHERE session_id IN ({marks}) GROUP BY region_id
            """,
            tuple(s["session_id"] for s in other_sessions),
        ):
            other_ws[r["region_id"]] = r
        region_ids |= set(other_ws)
        co_edges = conn.execute(
            f"SELECT COUNT(*) AS n FROM co_access WHERE session_id IN ({marks})", tuple(s["session_id"] for s in other_sessions)
        ).fetchone()["n"]
    else:
        co_edges = 0
    co_edges_session = conn.execute("SELECT COUNT(*) AS n FROM co_access WHERE session_id = ?", (sid or "",)).fetchone()["n"]

    meta: dict[str, dict] = {}
    if region_ids:
        ids = list(region_ids)
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            marks = ",".join("?" * len(chunk))
            for r in _rows(
                conn,
                f"SELECT region_id, path, symbol, kind, start_line, end_line, token_count FROM source_regions WHERE region_id IN ({marks})",
                tuple(chunk),
            ):
                meta[r["region_id"]] = r
    hints = _symbol_hints(events, bundle)

    # First admit / prefetch turn per region, and "used after admission" set.
    admitted_turn: dict[str, int] = {}
    admitted_ms: dict[str, int] = {}
    used_after: set[str] = set()
    for e in events:
        op = e["operation"]
        if e["source"] == "controller" and op in ("admit", "prefetch"):
            for rid in e["regions"]:
                admitted_turn.setdefault(rid, e["turn"])
                admitted_ms.setdefault(rid, e["timestamp_ms"])
        elif (e["source"] == "agent_tool" and op in AGENT_READ_OPS) or (e["source"] == "controller" and op == "hit"):
            for rid in e["regions"]:
                if rid in admitted_ms and e["timestamp_ms"] >= admitted_ms[rid]:
                    used_after.add(rid)

    def describe(rid: str) -> dict:
        m = meta.get(rid)
        w = ws.get(rid, {})
        h = hints.get(rid, {})
        succ_meta = meta.get(w.get("replaced_by") or "", {})
        if m is None:
            m = {
                "path": h.get("path") or succ_meta.get("path") or "(removed)",
                "symbol": h.get("symbol") or succ_meta.get("symbol") or "(invalidated region)",
                "kind": succ_meta.get("kind"),
                "start_line": succ_meta.get("start_line") or 0,
                "end_line": succ_meta.get("end_line") or 0,
                "token_count": int(w.get("token_cost") or succ_meta.get("token_count") or 1),
                "ghost": True,
            }
        be = entries.get(rid)
        pf = prefetch.get(rid)
        repr_level = (be or {}).get("level") or ("signature" if pf else None)
        served = (be or {}).get("token_count")
        if served is None and pf:
            served = max(1, len(pf.get("signature") or "") // 4)
        successor = w.get("replaced_by")
        return {
            "region_id": rid,
            "symbol": m.get("symbol") or m.get("path"),
            "path": m.get("path"),
            "kind": m.get("kind"),
            "start_line": m.get("start_line"),
            "end_line": m.get("end_line"),
            "token_count": int(m.get("token_count") or 0),
            "served_tokens": served,
            "repr": repr_level,
            "compressed": repr_level in COMPRESSED_LEVELS,
            "score": (be or {}).get("score") if be else (pf or {}).get("score"),
            "p_used_soon": (be or {}).get("p_used_soon") if be else (pf or {}).get("p_used_soon"),
            "parts": (be or {}).get("parts"),
            "why": (be or {}).get("why") or (pf or {}).get("why") or w.get("explanation"),
            "admit": (be or {}).get("admit"),
            "pinned": bool(w.get("pinned")),
            "stale": bool(w.get("stale")),
            "admitted": bool(w.get("admitted")),
            "prefetched": pf is not None,
            "frame": rid in frame_ids,
            "replaced_by": successor,
            "successor_symbol": (meta.get(successor) or hints.get(successor) or {}).get("symbol") if successor else None,
            "admitted_at": admitted_turn.get(rid),
            "last_access_turn": w.get("last_access_turn"),
            "execution_score": round(float(w.get("execution_score") or 0), 3),
            "agent_access_score": round(float(w.get("agent_access_score") or 0), 3),
            "co_access_score": round(float(w.get("co_access_score") or 0), 3),
            "edit_likelihood": round(float(w.get("edit_likelihood") or 0), 3),
            "used_after_admit": rid in used_after,
            "ghost": bool(m.get("ghost")),
        }

    # ---- assign residents
    l0: list[dict] = []
    l1: list[dict] = []
    l2: list[dict] = []
    placed: set[str] = set()
    for rid in region_ids:
        w = ws.get(rid, {})
        in_bundle = rid in entries or rid in prefetch
        level = _level_for(w, in_bundle, rid in frame_ids, rid in other_ws)
        d = describe(rid)
        d["level"] = level
        if level == "L0":
            l0.append(d)
            # Pinned anchors are also what the bundle serves, so a fresh pinned
            # entry occupies L1 as well (same bytes, two tags) — mirror it.
            if in_bundle and not d["stale"]:
                l1.append({**d, "level": "L1", "mirror_of": "L0"})
        elif level == "L1":
            l1.append(d)
        elif level == "L2":
            o = other_ws.get(rid)
            d["scope"] = "repo" if (o and not w) else "session"
            d["other_sessions"] = int((o or {}).get("sessions") or 0)
            if o and not w:
                d["execution_score"] = round(float(o.get("execution_score") or 0), 3)
                d["agent_access_score"] = round(float(o.get("agent_access_score") or 0), 3)
                d["last_access_turn"] = o.get("last_access_turn")
            l2.append(d)
        else:
            continue
        placed.add(rid)

    def sort_key(d: dict):
        return (d["stale"], not d["pinned"], -(d.get("score") or 0), -(d.get("execution_score") or 0), d["symbol"] or "")

    l0.sort(key=sort_key)
    l1.sort(key=sort_key)
    l2.sort(key=sort_key)

    def tokens(items: list[dict], *, served: bool, fresh_only: bool) -> int:
        tot = 0
        for d in items:
            if fresh_only and d["stale"]:
                continue
            if d.get("mirror_of"):
                pass  # mirrored anchors are counted once, in L1 occupancy
            v = d.get("served_tokens") if served else None
            tot += int(v if v is not None else d["token_count"])
        return tot

    l1_fresh = [d for d in l1 if not d["stale"]]
    l1_used = (bundle or {}).get("token_count")
    if l1_used is None:
        l1_used = tokens(l1_fresh, served=True, fresh_only=True)
    l1_used = int(l1_used) + sum(int(d.get("served_tokens") or 0) for d in l1_fresh if d["prefetched"] and d["region_id"] not in entries)
    l1_stale_tokens = tokens([d for d in l1 if d["stale"]], served=True, fresh_only=False)

    l0_tokens = tokens([d for d in l0 if not d["stale"]], served=False, fresh_only=True)
    l0_capacity = max(l0_tokens, 1)

    levels = {
        "L0": {
            "label": LEVEL_LABELS["L0"],
            "description": "pinned anchors: failing-test frames, edited regions, current diff",
            "capacity": l0_capacity,
            "capacity_kind": "pinned",
            "used": l0_tokens,
            "occupancy": 1.0 if l0 else 0.0,
            "count": len(l0),
            "stale": sum(1 for d in l0 if d["stale"]),
            "entries": l0,
        },
        "L1": {
            "label": LEVEL_LABELS["L1"],
            "description": "regions admitted to the current bundle under the token budget",
            "capacity": budget,
            "capacity_kind": "tokens",
            "used": l1_used,
            "free": max(0, budget - l1_used),
            "stale_tokens": l1_stale_tokens,
            "occupancy": round(l1_used / budget, 4) if budget else 0.0,
            "count": len(l1),
            "stale": sum(1 for d in l1 if d["stale"]),
            "compressed": sum(1 for d in l1 if d["compressed"] and not d["stale"]),
            "exact": sum(1 for d in l1 if d["repr"] == "exact" and not d["stale"]),
            "prefetched": sum(1 for d in l1 if d["prefetched"]),
            "entries": l1,
        },
        "L2": {
            "label": LEVEL_LABELS["L2"],
            "description": "working-set memory and co-access edges learned across sessions on this repo",
            "capacity": None,
            "capacity_kind": "unbounded",
            "used": tokens(l2, served=False, fresh_only=False),
            "occupancy": None,
            "count": len(l2),
            "stale": sum(1 for d in l2 if d["stale"]),
            "session_scope": sum(1 for d in l2 if d.get("scope") == "session"),
            "repo_scope": sum(1 for d in l2 if d.get("scope") == "repo"),
            "co_access_edges": int(co_edges_session),
            "co_access_edges_history": int(co_edges),
            "other_sessions": len(other_sessions),
            "entries": l2[:80],
        },
        "backing": {
            "label": LEVEL_LABELS["backing"],
            "description": "every indexed source region (hash-versioned)",
            "capacity": int(totals["t"]),
            "capacity_kind": "tokens",
            "used": int(totals["t"]),
            "occupancy": 1.0,
            "count": int(totals["n"]),
            "files": int(n_files),
            "by_kind": by_kind,
            "resident": len(placed),
            "entries": [],
        },
    }

    # ---- activity log (last 60 controller ops)
    log: list[dict] = []
    for e in reversed(events):
        op = e["operation"]
        if op not in LOG_OPS:
            continue
        extra = e.get("extra") or {}
        regions = e["regions"]
        syms: list[str] = []
        if extra.get("symbol"):
            syms = [extra["symbol"]]
        elif extra.get("symbols"):
            syms = [s for s in extra["symbols"] if s]
        else:
            for rid in regions[:6]:
                m = meta.get(rid) or hints.get(rid) or {}
                syms.append(m.get("symbol") or (rid[:8] + "…"))
        reason = extra.get("why") or extra.get("reason") or extra.get("admit")
        if op == "prefetch" and extra.get("p") is not None:
            reason = f"p_used_soon={extra['p']}" + (f" ← {hints.get(extra.get('from'), {}).get('symbol') or (meta.get(extra.get('from')) or {}).get('symbol') or ''}" if extra.get("from") else "")
        if op == "bundle_served":
            reason = f"{len(regions)} regions · {e.get('tokens_returned') or 0} tok · {extra.get('n_rejected', 0)} rejected"
        if op == "invalidate":
            reason = f"{extra.get('reason', 'hash mismatch')} · {e.get('query') or ''}".strip(" ·")
        if op == "evict" and extra.get("keep") is not None:
            reason = f"{extra.get('reason') or 'over budget'} (keep={extra['keep']})"
        log.append(
            {
                "event_id": e["event_id"],
                "op": op,
                "turn": e["turn"],
                "timestamp_ms": e["timestamp_ms"],
                "regions": regions[:12],
                "symbols": syms[:6],
                "n": len(regions),
                "reason": reason,
                "level": extra.get("level"),
                "score": extra.get("score"),
                "replaced_by": extra.get("replaced_by") if op == "invalidate" else None,
            }
        )
        if len(log) >= 60:
            break

    # ---- fragmentation / pollution (honest numbers)
    admitted_ids = set(admitted_turn)
    pollution_ids = [rid for rid in admitted_ids if rid not in used_after]
    prefetched_ids = {e_rid for e in events if e["source"] == "controller" and e["operation"] == "prefetch" for e_rid in e["regions"]}
    hits = sum(max(1, len(e["regions"])) for e in events if e["source"] == "controller" and e["operation"] == "hit")
    misses = sum(max(1, len(e["regions"])) for e in events if e["source"] == "controller" and e["operation"] == "miss")
    stats = {
        "budget": budget,
        "bundle_tokens": int(l1_used),
        "free_tokens": max(0, budget - int(l1_used)),
        "utilization": round(l1_used / budget, 4) if budget else 0.0,
        "stale_tokens": int(l1_stale_tokens),
        "entries": len(l1_fresh),
        "exact": levels["L1"]["exact"],
        "compressed": levels["L1"]["compressed"],
        "compression_ratio": round(
            1 - (sum(int(d.get("served_tokens") or d["token_count"]) for d in l1_fresh) / max(1, sum(d["token_count"] for d in l1_fresh))),
            3,
        )
        if l1_fresh
        else 0.0,
        "admitted_total": len(admitted_ids),
        "pollution": len(pollution_ids),
        "pollution_rate": round(len(pollution_ids) / len(admitted_ids), 3) if admitted_ids else None,
        "prefetches": len(prefetched_ids),
        "prefetch_used": sum(1 for r in prefetched_ids if r in used_after),
        "hits": hits,
        "misses": misses,
        "hit_rate": round(hits / (hits + misses), 3) if (hits + misses) else None,
        "evictions": sum(1 for e in events if e["operation"] == "evict"),
        "invalidations": sum(1 for e in events if e["operation"] == "invalidate"),
        "stale_blocked": sum(1 for e in events if e["operation"] == "stale_blocked"),
        "stale_entries": sum(1 for w in ws_rows if w.get("stale")),
    }

    return {
        "session_id": sid,
        "repo_id": repo_id,
        "condition": session.get("condition"),
        "task_id": session.get("task_id"),
        "objective": (bundle or {}).get("objective") or session.get("objective"),
        "bundle_id": (bundle or {}).get("bundle_id"),
        "bundle_created_ms": (bundle or {}).get("created_ms"),
        "turn": max((e["turn"] for e in events), default=0),
        "token_budget": budget,
        "levels": levels,
        "order": list(LEVELS),
        "log": log,
        "stats": stats,
    }


# --------------------------------------------------------------------- router

router = APIRouter(prefix="/api/graph", tags=["graph"])


def _runtime_from(request: Request):
    rt = getattr(request.app.state, "runtime", None)
    if rt is None:
        from traceweaver.runtime import TraceWeaverRuntime

        rt = TraceWeaverRuntime()
        request.app.state.runtime = rt
    return rt


@router.get("")
def get_graph(request: Request, session_id: str | None = None, max_nodes: int = 160):
    return graph_payload(_runtime_from(request), session_id, max_nodes=max(8, min(int(max_nodes), 600)))


@router.get("/hierarchy")
def get_hierarchy(request: Request, session_id: str | None = None):
    return hierarchy_payload(_runtime_from(request), session_id)


def make_router(runtime) -> APIRouter:
    """Router bound to an explicit runtime (no reliance on ``app.state``)."""
    r = APIRouter(prefix="/api/graph", tags=["graph"])

    @r.get("")
    def _graph(session_id: str | None = None, max_nodes: int = 160):
        return graph_payload(runtime, session_id, max_nodes=max(8, min(int(max_nodes), 600)))

    @r.get("/hierarchy")
    def _hierarchy(session_id: str | None = None):
        return hierarchy_payload(runtime, session_id)

    return r


__all__ = ["router", "make_router", "graph_payload", "hierarchy_payload"]
