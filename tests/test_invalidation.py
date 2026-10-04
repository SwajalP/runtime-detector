from pathlib import Path

from ledger.config import LedgerConfig
from ledger.policy.invalidation import invalidate_path
from ledger.runtime import LedgerRuntime


def test_edit_invalidates_hash():
    demo = Path(__file__).resolve().parents[1] / "demo_repo"
    cfg = LedgerConfig.from_root(demo)
    rt = LedgerRuntime(cfg)
    rt.reindex()
    sid = rt.new_session(agent="test")
    rel = "shop/billing/discount_policy.py"
    path = cfg.repo_root / rel
    original = path.read_text()
    region = rt.conn.execute(
        "SELECT region_id, content_hash FROM source_regions WHERE path = ? AND kind = 'function' AND symbol LIKE '%for_renewal%'",
        (rel,),
    ).fetchone()
    assert region
    try:
        path.write_text(original + "\n# touched\n")
        stale = invalidate_path(rt.conn, cfg, sid, rel, rt.collector)
        assert stale
        events = rt.collector.list_events(sid)
        assert any(e["operation"] == "invalidate" for e in events)
    finally:
        path.write_text(original)
        rt.reindex()
