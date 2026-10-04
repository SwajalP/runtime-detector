from __future__ import annotations

from traceweaver.eval.runner import load_tasks, run_task


def test_index_finds_for_renewal(demo_rt):
    stats = demo_rt.reindex()
    assert stats["regions"] > 10
    rows = demo_rt.conn.execute(
        "SELECT symbol, path FROM source_regions WHERE symbol LIKE '%for_renewal%'"
    ).fetchall()
    assert any("discount_policy.py" in r["path"] for r in rows)


def test_bundle_after_execution_prefers_discount_policy(demo_rt):
    tasks = {t["id"]: t for t in load_tasks(demo_rt.cfg.repo_root)}
    result = run_task(demo_rt, tasks["renewal-discount"], use_traceweaver=True)
    assert result["success"]
    assert "shop/billing/discount_policy.py" in result["observed_targets"]
