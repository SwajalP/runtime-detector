"""Prefetch is unused call successors of HIGH-score parents — quality over quantity."""

from __future__ import annotations

import json

from ledger.eval.runner import load_tasks, run_task


def test_prefetch_fires_call_edge_neighbors_on_renewal_discount(demo_rt):
    tasks = {t["id"]: t for t in load_tasks(demo_rt.cfg.repo_root)}
    result = run_task(demo_rt, tasks["renewal-discount"], use_ledger=True)
    assert result["success"]
    assert 1 <= result["prefetches"] <= demo_rt.cfg.prefetch_limit
    assert result["prefetch_precision"] is not None
    assert result["prefetch_precision"] >= 0.3

    row = demo_rt.conn.execute(
        "SELECT payload_json FROM bundles ORDER BY created_ms DESC LIMIT 1"
    ).fetchone()
    payload = json.loads(row["payload_json"])
    prefetch = payload.get("prefetch") or []
    assert 1 <= len(prefetch) <= 3

    entry_ids = {e["region_id"] for e in payload["entries"]}
    high_parents = [
        e
        for e in payload["entries"]
        if float(e.get("score") or 0) >= demo_rt.cfg.prefetch_parent_min_score
        and float(e.get("p_used_soon") or 0) >= demo_rt.cfg.prefetch_parent_min_p
        and not (e.get("path") or "").startswith("tests/")
    ]
    assert high_parents, "demo task should admit at least one HIGH-score production region"

    for p in prefetch:
        assert p["region_id"] not in entry_ids
        assert p["p_used_soon"] >= demo_rt.cfg.prefetch_threshold
        assert p.get("signature")
        assert not (p.get("path") or "").startswith("tests/")
        linked = any(
            demo_rt.conn.execute(
                "SELECT 1 FROM calls WHERE caller_id = ? AND callee_id = ?",
                (parent["region_id"], p["region_id"]),
            ).fetchone()
            for parent in high_parents
        )
        assert linked, f"{p['symbol']} is not an unused callee of a HIGH-score parent"
        assert "call-edge" in (p.get("why") or "")
        assert "successor" in (p.get("why") or "")

    assert payload["token_count"] <= payload["budget"]
    assert sum(int(p.get("token_count") or 0) for p in prefetch) < 200


def test_prefetch_does_not_spray_on_held_out_dunning(demo_rt):
    tasks = {t["id"]: t for t in load_tasks(demo_rt.cfg.repo_root)}
    result = run_task(demo_rt, tasks["held-out-dunning"], use_ledger=True)
    assert result["success"]
    assert result["prefetches"] == 0
    assert result["prefetch_precision"] is None
