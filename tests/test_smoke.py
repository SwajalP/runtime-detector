from traceweaver.mcp.tools import render_bundle_text
from traceweaver.trace.runner import run_pytest_traced


def test_index_exec_bundle_smoke(demo_rt):
    rows = demo_rt.conn.execute(
        "SELECT symbol, path FROM source_regions WHERE symbol LIKE '%for_renewal%'"
    ).fetchall()
    assert any("discount_policy.py" in r["path"] for r in rows)

    sid = demo_rt.new_session(agent="test", condition="traceweaver", task_id="renewal-discount")
    traced = run_pytest_traced(
        demo_rt.cfg,
        sid,
        args=["-q", "tests/test_renewal_discount.py"],
        conn=demo_rt.conn,
        collector=demo_rt.collector,
        controller=demo_rt.controller,
        task_id="renewal-discount",
    )
    assert traced["passed"] is False
    names = []
    for rid in traced["executed_region_ids"] + traced["frame_region_ids"] + traced["failing_only_region_ids"]:
        row = demo_rt.conn.execute("SELECT symbol, path FROM source_regions WHERE region_id = ?", (rid,)).fetchone()
        if row:
            names.append((row["symbol"], row["path"]))
    assert any("for_renewal" in (s or "") or "discount_policy.py" in (p or "") for s, p in names)

    bundle = demo_rt.controller.build_bundle(
        sid, objective="renewal invoices ignore loyalty discounts", seed="for_renewal"
    )
    assert any("discount_policy.py" in e["path"] or "for_renewal" in (e.get("symbol") or "") for e in bundle["entries"])
    text = render_bundle_text(bundle)
    assert "discount_policy" in text or "for_renewal" in text


def test_cli_lists_mcp_and_agentverse():
    from typer.testing import CliRunner

    from traceweaver.cli import app

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in ("mcp", "agentverse", "delete-session", "eval", "hook"):
        assert name in result.stdout
