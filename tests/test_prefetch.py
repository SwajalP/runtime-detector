"""Prefetch must fire 1–3 resolved call-edge neighbours on the demo task."""

from __future__ import annotations

import json

from ledger.eval.runner import load_tasks, run_task


def test_prefetch_fires_call_edge_neighbors_on_renewal_discount(demo_rt):
    tasks = {t["id"]: t for t in load_tasks(demo_rt.cfg.repo_root)}
    result = run_task(demo_rt, tasks["renewal-discount"], use_ledger=True)
    assert result["success"]
    assert 1 <= result["prefetches"] <= demo_rt.cfg.prefetch_limit

    row = demo_rt.conn.execute(
        "SELECT payload_json FROM bundles ORDER BY created_ms DESC LIMIT 1"
    ).fetchone()
    payload = json.loads(row["payload_json"])
    prefetch = payload.get("prefetch") or []
    assert 1 <= len(prefetch) <= 3

    entry_ids = {e["region_id"] for e in payload["entries"]}
    for p in prefetch:
        assert p["region_id"] not in entry_ids
        assert p["p_used_soon"] >= demo_rt.cfg.prefetch_threshold
        assert p.get("signature")
        # resolved call edge from an admitted region
        linked = demo_rt.conn.execute(
            """
            SELECT 1 FROM calls
            WHERE callee_id = ? OR caller_id = ?
            """,
            (p["region_id"], p["region_id"]),
        ).fetchone()
        assert linked, f"{p['symbol']} is not on the call graph"
        assert "call-edge" in (p.get("why") or "")

    # Signatures are cheap; prefetch must not blow the budget.
    assert payload["token_count"] <= payload["budget"]
    assert sum(int(p.get("token_count") or 0) for p in prefetch) < 200
