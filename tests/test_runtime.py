from __future__ import annotations

from pathlib import Path

from ledger.config import LedgerConfig
from ledger.runtime import LedgerRuntime


def test_index_finds_for_renewal(tmp_path: Path):
    # Use the demo repo if present next to the package
    demo = Path(__file__).resolve().parents[1] / "demo_repo"
    cfg = LedgerConfig.from_root(demo)
    rt = LedgerRuntime(cfg)
    stats = rt.reindex()
    assert stats["regions"] > 10
    rows = rt.conn.execute(
        "SELECT symbol, path FROM source_regions WHERE symbol LIKE '%for_renewal%'"
    ).fetchall()
    assert any("discount_policy.py" in r["path"] for r in rows)


def test_bundle_after_execution_prefers_discount_policy():
    demo = Path(__file__).resolve().parents[1] / "demo_repo"
    cfg = LedgerConfig.from_root(demo)
    rt = LedgerRuntime(cfg)
    rt.reindex()
    from ledger.eval.runner import load_tasks, run_task

    tasks = {t["id"]: t for t in load_tasks(demo)}
    result = run_task(rt, tasks["renewal-discount"], use_ledger=True)
    assert result["success"]
    assert "shop/billing/discount_policy.py" in result["observed_targets"]
