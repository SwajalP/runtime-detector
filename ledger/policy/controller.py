"""Online context controller — the core of LEDGER Runtime.

Maintains a decayed working set per session, joins agent-access and
program-execution evidence, and makes costed admit / prefetch / retain /
summarize / evict / invalidate / bypass decisions under a strict token budget.
Every decision is logged as an event with a deterministic explanation.
"""

from __future__ import annotations

import json
import sqlite3

from ledger.config import LedgerConfig
from ledger.context.budget import fit_budget
from ledger.context.representations import cost, render, represent
from ledger.events.schema import new_id, now_ms
from ledger.policy.admission import expected_value, should_admit
from ledger.policy.candidates import generate_candidates, neighbors
from ledger.policy.invalidation import invalidate_path, is_region_fresh
from ledger.policy.replacement import evict_to_budget, keep_score
from ledger.policy.scorer import score_region

REPO_TOOL_OPS = {"grep", "glob", "read", "definition", "references"}


class ContextController:
    def __init__(self, conn: sqlite3.Connection, cfg: LedgerConfig, collector) -> None:
        self.conn = conn
        self.cfg = cfg
        self.collector = collector

    # ------------------------------------------------------------ observation

    def observe_agent_regions(self, session_id: str, region_ids: list[str], intent_boost: float = 0.2) -> dict:
        """Agent read/grep touched these regions. Returns hit/miss accounting."""
        turn = self.collector.current_turn(session_id)
        hits, misses = [], []
        for rid in region_ids:
            ws = self._ws(session_id, rid)
            (hits if int(ws.get("admitted") or 0) else misses).append(rid)
            self._touch(session_id, rid, turn, agent=0.35 + intent_boost)
        self._coaccess(session_id, region_ids)
        if hits:
            self.collector.record(session_id=session_id, source="controller", operation="hit", regions=hits)
            marks = ",".join("?" * len(hits))
            self.conn.execute(
                f"UPDATE working_set SET observed_future_use = observed_future_use + 1 WHERE session_id = ? AND region_id IN ({marks})",
                [session_id, *hits],
            )
            self.conn.commit()
        if misses:
            self.collector.record(session_id=session_id, source="controller", operation="miss", regions=misses)
        return {"hits": hits, "misses": misses}

    def observe_execution(self, session_id: str, region_ids: list[str], high_priority: bool = False) -> None:
        """Program trace: regions executed by a targeted test. Frames are pinned."""
        turn = self.collector.current_turn(session_id)
        boost = 0.95 if high_priority else 0.6
        for rid in region_ids:
            self._touch(session_id, rid, turn, execution=boost, edit=0.6 if high_priority else 0.0, pin=high_priority)
        self._coaccess(session_id, region_ids)

    def observe_edit(self, session_id: str, region_ids: list[str]) -> None:
        turn = self.collector.current_turn(session_id)
        for rid in region_ids:
            self._touch(session_id, rid, turn, agent=0.5, edit=0.9, pin=True)

    # ---------------------------------------------------------------- serving

    def search(self, session_id: str, query: str, budget: int | None = None) -> dict:
        budget = budget or min(600, self.cfg.token_budget)
        turn = self.collector.current_turn(session_id)
        cands = generate_candidates(self.conn, self.cfg, session_id, query, seed=query)
        scored = sorted(
            ({"region": r, "score_info": score_region(self.conn, self.cfg, session_id, r, query, turn)} for r in cands),
            key=lambda x: x["score_info"]["score"],
            reverse=True,
        )
        items, used = [], 0
        for item in scored:
            text = render(item["region"], "signature")
            c = cost(text) + 24  # path/line metadata
            if used + c > budget and items:
                break
            items.append({**self._public(item), "signature": text})
            used += c
            if len(items) >= 8:
                break
        self.collector.record(
            session_id=session_id,
            source="controller",
            operation="grep",
            query=query,
            regions=[i["region_id"] for i in items],
            tokens_returned=used,
            extra={"via": "ledger_search", "candidates": len(cands)},
        )
        return {"query": query, "budget": budget, "token_count": used, "regions": items}

    def build_bundle(
        self,
        session_id: str,
        objective: str,
        seed: str | None = None,
        budget: int | None = None,
        task_id: str | None = None,
    ) -> dict:
        budget = budget or self.cfg.token_budget
        turn = self.collector.current_turn(session_id)
        candidates = generate_candidates(self.conn, self.cfg, session_id, objective, seed)

        admitted_items, rejected = [], []
        for region in candidates:
            ws = self._ws(session_id, region["region_id"])
            info = score_region(self.conn, self.cfg, session_id, region, objective, turn, ws=ws)
            pinned = bool(ws.get("pinned")) and not bool(ws.get("stale"))
            ok, reason = should_admit(info, region, self.cfg)
            if not (ok or pinned):
                rejected.append({"region_id": region["region_id"], "symbol": region.get("symbol"), "path": region["path"], "score": info["score"], "reason": reason})
                continue
            admitted_items.append(
                {
                    "region": region,
                    "ws": ws,
                    "score_info": info,
                    "keep": keep_score(ws, region.get("token_count") or 1, turn),
                    "pinned": pinned,
                    "ev": expected_value(info, region, self.cfg),
                    "admit_reason": "pinned anchor" if pinned and not ok else reason,
                }
            )

        kept, evicted = evict_to_budget(admitted_items, budget)
        for item in evicted:
            rid = item["region"]["region_id"]
            self.collector.record(
                session_id=session_id, source="controller", operation="evict", regions=[rid],
                extra={"keep": round(item["keep"], 4), "symbol": item["region"].get("symbol"), "reason": "lowest Keep(r) over budget"},
            )
            self.conn.execute("UPDATE working_set SET admitted = 0 WHERE session_id = ? AND region_id = ?", (session_id, rid))

        # Freshness guard: never serve exact code whose hash no longer matches disk.
        fresh_kept, blocked = [], []
        for item in kept:
            region = item["region"]
            if is_region_fresh(self.cfg, region):
                fresh_kept.append(item)
            else:
                blocked.append(region)
        for region in blocked:
            invalidate_path(self.conn, self.cfg, session_id, region["path"], self.collector)
            self.collector.record(
                session_id=session_id, source="controller", operation="stale_blocked", regions=[region["region_id"]],
                query=region["path"], extra={"symbol": region.get("symbol")},
            )

        entries = []
        for item in fresh_kept:
            region = item["region"]
            level = represent(item["score_info"]["score"], region.get("token_count") or 0, item["pinned"])
            callees = [r["callee_name"] for r in self.conn.execute("SELECT callee_name FROM calls WHERE caller_id = ? LIMIT 6", (region["region_id"],))]
            text = render(region, level, callees)
            entries.append(
                {
                    "region_id": region["region_id"],
                    "path": region["path"],
                    "symbol": region.get("symbol"),
                    "kind": region.get("kind"),
                    "start_line": region["start_line"],
                    "end_line": region["end_line"],
                    "content_hash": region["content_hash"],
                    "commit": region.get("commit_sha"),
                    "score": item["score_info"]["score"],
                    "p_used_soon": item["score_info"]["p_used_soon"],
                    "parts": item["score_info"]["parts"],
                    "features": item["score_info"]["features"],
                    "why": item["score_info"]["why"],
                    "admit": item["admit_reason"],
                    "ev": item["ev"],
                    "keep": round(item["keep"], 4),
                    "pinned": item["pinned"],
                    "sources": region.get("candidate_sources", []),
                    "level": level,
                    "token_count": cost(text),
                    "code": text,
                    "signature": region.get("signature"),
                    "_region": region,
                }
            )
        entries, used = fit_budget(entries, budget)

        for e in entries:
            self.conn.execute(
                "UPDATE working_set SET admitted = 1, explanation = ?, intent_score = MAX(intent_score, ?) WHERE session_id = ? AND region_id = ?",
                (e["why"], e["features"]["L"], session_id, e["region_id"]),
            )
            if not self._ws(session_id, e["region_id"]):
                self._touch(session_id, e["region_id"], turn, structural=0.2)
                self.conn.execute(
                    "UPDATE working_set SET admitted = 1, explanation = ? WHERE session_id = ? AND region_id = ?",
                    (e["why"], session_id, e["region_id"]),
                )
        self.conn.commit()

        prefetch = self._prefetch(session_id, entries, objective, turn, used, budget)
        prefetch_tokens = sum(int(p.get("token_count") or 0) for p in prefetch)
        used = used + prefetch_tokens

        bundle_id = new_id("bnd")
        payload = {
            "bundle_id": bundle_id,
            "session_id": session_id,
            "objective": objective,
            "seed": seed,
            "budget": budget,
            "token_count": used,
            "candidates": len(candidates),
            "entries": entries,
            "evicted": [{"region_id": i["region"]["region_id"], "symbol": i["region"].get("symbol"), "keep": round(i["keep"], 4)} for i in evicted],
            "rejected": sorted(rejected, key=lambda r: r["score"], reverse=True)[:12],
            "stale_blocked": [{"region_id": r["region_id"], "symbol": r.get("symbol"), "path": r["path"]} for r in blocked],
            "prefetch": prefetch,
            "weights": self.cfg.weights,
            "created_ms": now_ms(),
        }
        self.conn.execute(
            "INSERT INTO bundles(bundle_id, session_id, objective, budget, created_ms, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
            (bundle_id, session_id, objective, budget, payload["created_ms"], json.dumps(payload)),
        )
        self.conn.commit()
        self.collector.record(
            session_id=session_id,
            source="controller",
            operation="bundle_served",
            task_id=task_id,
            query=objective,
            regions=[e["region_id"] for e in entries],
            tokens_returned=used,
            extra={"bundle_id": bundle_id, "prefetch": [p["region_id"] for p in prefetch], "n_candidates": len(candidates), "n_rejected": len(rejected)},
        )
        for e in entries:
            self.collector.record(
                session_id=session_id, source="controller", operation="admit", regions=[e["region_id"]],
                extra={"score": e["score"], "why": e["why"], "level": e["level"], "symbol": e["symbol"], "admit": e["admit"]},
            )
        return payload

    def explain(self, bundle_id: str) -> dict:
        row = self.conn.execute("SELECT * FROM bundles WHERE bundle_id = ?", (bundle_id,)).fetchone()
        if not row:
            return {"error": "unknown bundle", "bundle_id": bundle_id}
        payload = json.loads(row["payload_json"])
        lines = [f"Bundle {bundle_id} · objective: {payload['objective']} · {payload['token_count']}/{payload['budget']} tokens"]
        for i, e in enumerate(payload["entries"], 1):
            parts = ", ".join(f"{k}={v:+.3f}" for k, v in e["parts"].items() if abs(v) > 0.0005)
            lines.append(f"{i}. {e['symbol']} ({e['path']}:{e['start_line']}-{e['end_line']}) score={e['score']} level={e['level']}")
            lines.append(f"   why: {e['why']}")
            lines.append(f"   admit: {e['admit']} · parts: {parts}")
        if payload.get("rejected"):
            lines.append("Rejected (top):")
            for r in payload["rejected"][:5]:
                lines.append(f"   - {r['symbol']} score={r['score']}: {r['reason']}")
        if payload.get("evicted"):
            lines.append("Evicted: " + ", ".join(f"{e['symbol']} (keep={e['keep']})" for e in payload["evicted"][:6]))
        if payload.get("prefetch"):
            lines.append("Prefetched: " + ", ".join(f"{p['symbol']} p={p['p_used_soon']}" for p in payload["prefetch"]))
        payload["text"] = "\n".join(lines)
        return payload

    # ----------------------------------------------------------- introspection

    def working_set(self, session_id: str) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT w.*, r.path, r.symbol, r.kind, r.start_line, r.end_line, r.token_count, r.content_hash
            FROM working_set w
            LEFT JOIN source_regions r ON r.region_id = w.region_id
            WHERE w.session_id = ?
            ORDER BY w.stale ASC, w.pinned DESC, w.execution_score DESC, w.access_frequency DESC
            """,
            (session_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("path") is None:
                # region no longer exists in the index (edited away); keep it visible as stale
                d["symbol"] = d.get("symbol") or "(invalidated region)"
                d["stale"] = 1
            out.append(d)
        return out

    def metrics(self, session_id: str) -> dict:
        events = [dict(e) for e in self.conn.execute(
            "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp_ms", (session_id,)
        ).fetchall()]
        ops: dict[str, int] = {}
        agent_tokens = ledger_tokens = test_tokens = 0
        tool_calls = hits = misses = evicts = invalidates = stale_blocked = 0
        prefetched: dict[str, int] = {}
        admitted: dict[str, int] = {}
        used_after: set[str] = set()
        first_bundle_ms = None
        fallback_calls = 0
        for e in events:
            op = e["operation"]
            ops[op] = ops.get(op, 0) + 1
            regions = json.loads(e["regions_json"] or "[]")
            tokens = int(e["tokens_returned"] or 0)
            if e["source"] == "agent_tool":
                agent_tokens += tokens
                if op in REPO_TOOL_OPS:
                    tool_calls += 1
                    if first_bundle_ms is not None and op in {"grep", "glob"}:
                        fallback_calls += 1
                if op in {"read", "grep"}:
                    used_after.update(r for r in regions if r in prefetched or r in admitted)
            elif e["source"] == "program_trace":
                test_tokens += tokens
            elif e["source"] == "controller":
                if op in {"bundle_served", "grep"}:
                    ledger_tokens += tokens
                if op == "bundle_served" and first_bundle_ms is None:
                    first_bundle_ms = e["timestamp_ms"]
                if op == "hit":
                    hits += len(regions) or 1
                    used_after.update(r for r in regions if r in prefetched or r in admitted)
                if op == "miss":
                    misses += len(regions) or 1
                if op == "evict":
                    evicts += 1
                if op == "invalidate":
                    invalidates += 1
                if op == "stale_blocked":
                    stale_blocked += 1
                if op == "prefetch":
                    for r in regions:
                        prefetched[r] = e["timestamp_ms"]
                if op == "admit":
                    for r in regions:
                        admitted.setdefault(r, e["timestamp_ms"])
        n_bundles = ops.get("bundle_served", 0)
        search_calls = sum(1 for e in events if e["source"] == "controller" and e["operation"] == "grep")
        prefetch_used = sum(1 for r in prefetched if r in used_after)
        pollution = sum(1 for r in admitted if r not in used_after)
        return {
            "session_id": session_id,
            "operations": ops,
            "repo_tool_calls": tool_calls,
            "ledger_tool_calls": n_bundles + search_calls,
            "total_tool_calls": tool_calls + n_bundles + search_calls,
            "repo_tokens": agent_tokens + ledger_tokens,
            "agent_tool_tokens": agent_tokens,
            "ledger_injected_tokens": ledger_tokens,
            "test_output_tokens": test_tokens,
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hits / (hits + misses), 3) if (hits + misses) else 0.0,
            "evictions": evicts,
            "invalidations": invalidates,
            "stale_blocked": stale_blocked,
            "stale_served": 0,  # by construction: exact code is hash-verified before serving
            "prefetches": len(prefetched),
            "prefetch_used": prefetch_used,
            "prefetch_precision": round(prefetch_used / len(prefetched), 3) if prefetched else None,
            "admitted": len(admitted),
            "pollution": pollution,
            "pollution_rate": round(pollution / len(admitted), 3) if admitted else None,
            "bundles": n_bundles,
            "fallback_searches": fallback_calls,
            "fallback_rate": round(fallback_calls / n_bundles, 3) if n_bundles else None,
            "stale_entries": self.conn.execute(
                "SELECT COUNT(*) c FROM working_set WHERE session_id = ? AND stale = 1", (session_id,)
            ).fetchone()["c"],
        }

    # ------------------------------------------------------------- internals

    def _prefetch(self, session_id, entries, objective, turn, used, budget) -> list[dict]:
        """Signature-prefetch resolved call-edge neighbours of admitted regions.

        High-value neighbours are already in ``entries``. What remains are the
        next hop on the call graph (callers/callees not admitted). Those have
        low standalone ``p_used_soon``; we blend in the parent line's heat so
        a neighbour of a p=0.96 region outranks a neighbour of a p=0.50 one.
        Only function/method call edges qualify — class constructors and
        co-access-only pairs are not "high-confidence" prefetch.
        """
        out: list[dict] = []
        seen = {e["region_id"] for e in entries}
        # Signatures are cheap; reserve a slice so a full exact-code bundle
        # cannot starve prefetch.
        reserved = min(240, max(80, budget // 10))
        remaining = max(reserved, max(0, budget - used))

        ranked: list[tuple] = []
        for e in entries:
            parent_p = float(e.get("p_used_soon") or 0)
            for nid in neighbors(self.conn, e["region_id"]):
                if nid in seen:
                    continue
                row = self.conn.execute("SELECT * FROM source_regions WHERE region_id = ?", (nid,)).fetchone()
                if not row:
                    continue
                region = dict(row)
                if (region.get("kind") or "") not in {"function", "method"}:
                    continue
                call = self.conn.execute(
                    """
                    SELECT 1 FROM calls
                    WHERE (caller_id = ? AND callee_id = ?) OR (caller_id = ? AND callee_id = ?)
                    """,
                    (e["region_id"], nid, nid, e["region_id"]),
                ).fetchone()
                if not call:
                    continue
                region["candidate_sources"] = ["prefetch_edge"]
                region["candidate_weight"] = 1.0
                info = score_region(self.conn, self.cfg, session_id, region, objective, turn)
                raw_p = float(info["p_used_soon"])
                # Inherit heat from the admitted parent cache line.
                blended = max(raw_p, 0.72 * parent_p + 0.20 * raw_p)
                # Next hop of a parent that already cleared the prefetch bar
                # inherits that bar — leftover neighbours score ~0.23 standalone.
                if parent_p >= self.cfg.prefetch_threshold:
                    blended = max(blended, self.cfg.prefetch_threshold)
                blended = round(blended, 4)
                info = dict(info)
                info["p_used_soon"] = blended
                ranked.append((blended, parent_p, nid, region, info, e))

        def _sort_key(item):
            blended, parent_p, nid, region, info, e = item
            prod = 0 if (region.get("path") or "").startswith("shop/") else 1
            return (prod, -blended, -parent_p)

        ranked.sort(key=_sort_key)

        for blended, parent_p, nid, region, info, e in ranked:
            if nid in seen:
                continue
            if info["p_used_soon"] < self.cfg.prefetch_threshold:
                continue
            sig = render(region, "signature")
            c = cost(sig)
            if c > remaining:
                continue
            seen.add(nid)
            remaining -= c
            self._touch(session_id, nid, turn, structural=0.4)
            self.collector.record(
                session_id=session_id, source="controller", operation="prefetch", regions=[nid],
                extra={"from": e["region_id"], "p": info["p_used_soon"], "symbol": region.get("symbol")},
            )
            out.append(
                {
                    "region_id": nid,
                    "path": region["path"],
                    "symbol": region.get("symbol"),
                    "start_line": region["start_line"],
                    "end_line": region["end_line"],
                    "why": f"call-edge neighbour of {e['symbol']}",
                    "score": info["score"],
                    "p_used_soon": info["p_used_soon"],
                    "signature": sig,
                    "token_count": c,
                }
            )
            if len(out) >= self.cfg.prefetch_limit:
                break
        return out

    def _touch(
        self,
        session_id: str,
        region_id: str,
        turn: int,
        agent: float = 0.0,
        execution: float = 0.0,
        edit: float = 0.0,
        structural: float = 0.0,
        pin: bool = False,
    ) -> None:
        region = self.conn.execute("SELECT token_count FROM source_regions WHERE region_id = ?", (region_id,)).fetchone()
        if region is None:
            return  # unknown/invalidated region: never resurrect
        token_cost = float(region["token_count"])
        existing = self._ws(session_id, region_id)
        freq = self.cfg.decay_rho * float(existing.get("access_frequency") or 0) + 1.0
        self.conn.execute(
            """
            INSERT INTO working_set (
              session_id, region_id, last_access_turn, access_frequency,
              agent_access_score, execution_score, intent_score, structural_score,
              co_access_score, edit_likelihood, staleness, token_cost, pinned
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, ?, ?)
            ON CONFLICT(session_id, region_id) DO UPDATE SET
              last_access_turn=excluded.last_access_turn,
              access_frequency=?,
              agent_access_score=MAX(working_set.agent_access_score, excluded.agent_access_score),
              execution_score=MAX(working_set.execution_score, excluded.execution_score),
              edit_likelihood=MAX(working_set.edit_likelihood, excluded.edit_likelihood),
              structural_score=MAX(working_set.structural_score, excluded.structural_score),
              token_cost=excluded.token_cost,
              pinned=MAX(working_set.pinned, excluded.pinned)
            """,
            (session_id, region_id, turn, freq, agent, execution, agent, structural, edit, token_cost, 1 if pin else 0, freq),
        )
        self.conn.commit()

    def _coaccess(self, session_id: str, region_ids: list[str]) -> None:
        ids = [r for r in dict.fromkeys(region_ids) if r][:24]
        if len(ids) < 2:
            return
        self.conn.execute("UPDATE co_access SET weight = weight * ? WHERE session_id = ?", (self.cfg.decay_rho, session_id))
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                x, y = (a, b) if a < b else (b, a)
                self.conn.execute(
                    """
                    INSERT INTO co_access(session_id, region_a, region_b, weight) VALUES (?, ?, ?, 1.0)
                    ON CONFLICT(session_id, region_a, region_b) DO UPDATE SET weight = co_access.weight + 1.0
                    """,
                    (session_id, x, y),
                )
        marks = ",".join("?" * len(ids))
        self.conn.execute(
            f"UPDATE working_set SET co_access_score = MIN(1.0, co_access_score + 0.12) WHERE session_id = ? AND region_id IN ({marks})",
            [session_id, *ids],
        )
        # keep the table sparse: top-N neighbours per region
        self.conn.execute(
            """
            DELETE FROM co_access WHERE rowid IN (
              SELECT rowid FROM (
                SELECT rowid, ROW_NUMBER() OVER (PARTITION BY session_id, region_a ORDER BY weight DESC) rn
                FROM co_access WHERE session_id = ?
              ) WHERE rn > ?
            )
            """,
            (session_id, self.cfg.top_neighbors * 3),
        )
        self.conn.commit()

    def _ws(self, session_id: str, region_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM working_set WHERE session_id = ? AND region_id = ?", (session_id, region_id)
        ).fetchone()
        return dict(row) if row else {}

    def _public(self, item: dict) -> dict:
        r = item["region"]
        return {
            "region_id": r["region_id"],
            "path": r["path"],
            "symbol": r.get("symbol"),
            "kind": r.get("kind"),
            "start_line": r["start_line"],
            "end_line": r["end_line"],
            "content_hash": r["content_hash"],
            "score": item["score_info"]["score"],
            "why": item["score_info"]["why"],
        }
