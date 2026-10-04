from traceweaver.policy.invalidation import invalidate_path


def test_edit_invalidates_hash(demo_rt):
    sid = demo_rt.new_session(agent="test")
    rel = "shop/billing/discount_policy.py"
    path = demo_rt.cfg.repo_root / rel
    original = path.read_text()
    region = demo_rt.conn.execute(
        "SELECT region_id, content_hash FROM source_regions WHERE path = ? AND kind = 'function' AND symbol LIKE '%for_renewal%'",
        (rel,),
    ).fetchone()
    assert region
    try:
        path.write_text(original + "\n# touched\n")
        stale = invalidate_path(demo_rt.conn, demo_rt.cfg, sid, rel, demo_rt.collector)
        assert stale
        events = demo_rt.collector.list_events(sid)
        assert any(e["operation"] == "invalidate" for e in events)
    finally:
        path.write_text(original)
        demo_rt.reindex()
