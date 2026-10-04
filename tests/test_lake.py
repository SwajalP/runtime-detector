"""Medallion lake: bronze events, silver region join, gold metrics."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from traceweaver.cli import app
from traceweaver.lake.pipeline import build_lake, lake_summary

runner = CliRunner()
SECRET_QUERY = "api_key=sekrit-value"


def _region(rt, symbol: str, path_substr: str) -> str:
    row = rt.conn.execute(
        "SELECT region_id FROM source_regions WHERE symbol = ? AND path LIKE ?",
        (symbol, f"%{path_substr}%"),
    ).fetchone()
    assert row is not None, symbol
    return row["region_id"]


def test_lake_build_joins_traced_regions_and_keeps_bronze_immutable(demo_rt, monkeypatch):
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
    renewal = _region(demo_rt, "for_renewal", "discount_policy.py")
    failing = _region(demo_rt, "test_renewal_applies_loyalty_on_annual_boundary", "test_renewal_discount.py")
    sid = demo_rt.new_session(agent="test", condition="traceweaver", task_id="renewal-discount")
    demo_rt.collector.record(
        session_id=sid,
        source="agent_tool",
        operation="read",
        query=SECRET_QUERY,
        regions=[renewal, failing],
        task_id="renewal-discount",
    )
    demo_rt.collector.record(
        session_id=sid,
        source="program_trace",
        operation="test",
        regions=[renewal, failing],
        task_id="renewal-discount",
    )
    demo_rt.controller.build_bundle(
        sid,
        objective="renewal invoices ignore loyalty discounts",
        seed="for_renewal",
    )
    (demo_rt.cfg.traceweaver_dir / "last_ab.json").write_text(
        json.dumps(
            {
                "source": "claude_hooks",
                "fixture": False,
                "task_id": "renewal-discount",
                "baseline": {"repo_tool_calls": 10, "repo_tokens": 1000},
                "traceweaver": {"repo_tool_calls": 4, "repo_tokens": 400},
            }
        ),
        encoding="utf-8",
    )
    before = [
        (row["event_id"], row["query"])
        for row in demo_rt.conn.execute("SELECT event_id, query FROM events ORDER BY event_id")
    ]

    report = build_lake(demo_rt.cfg)
    assert report["backend"] == "local-lake"
    assert report["databricks_called"] is False
    assert report["bronze"]["rows"] >= 2
    assert report["silver"]["rows"] >= 1
    assert report["gold"]["metrics"]["rows"] >= 1

    root = demo_rt.cfg.traceweaver_dir / "lake"
    bronze_text = (root / "bronze" / "events.jsonl").read_text(encoding="utf-8")
    silver_text = (root / "silver" / "region_observations.jsonl").read_text(encoding="utf-8")
    metrics = [
        json.loads(line)
        for line in (root / "gold" / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert "evt_" in bronze_text or "event_id" in bronze_text
    assert any(json.loads(line).get("source") == "agent_tool" for line in bronze_text.splitlines() if line.strip())
    assert "for_renewal" in silver_text
    assert "test_renewal_applies_loyalty_on_annual_boundary" in silver_text
    assert "sekrit-value" not in silver_text
    assert "[redacted]" in silver_text
    kinds = {row["kind"] for row in metrics}
    assert "ab" in kinds
    assert "audit" in kinds
    audit = next(row for row in metrics if row["kind"] == "audit")
    assert audit["fixture"] is False
    assert audit["largest_scc_size"] == 5
    assert audit["reverse_bfs_depth"] == 3
    assert audit["min_cut_seed_side_count"] == 21
    ab = next(row for row in metrics if row["kind"] == "ab")
    assert ab["source_file"] == "last_ab.json"
    assert ab["call_change_pct"] == -60.0
    assert ab["token_change_pct"] == -60.0

    after = [
        (row["event_id"], row["query"])
        for row in demo_rt.conn.execute("SELECT event_id, query FROM events ORDER BY event_id")
    ]
    assert after == before

    first_lines = [line for line in bronze_text.splitlines() if line.strip()]
    demo_rt.collector.record(
        session_id=sid,
        source="agent_tool",
        operation="grep",
        query="renewal",
        regions=[renewal],
    )
    build_lake(demo_rt.cfg)
    second_lines = [
        line
        for line in (root / "bronze" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for line in first_lines:
        assert line in second_lines
    assert len(second_lines) == len(first_lines) + 1

    summary = lake_summary(demo_rt.cfg)
    assert summary["backend"] == "local-lake"
    assert summary["bronze"] == len(second_lines)
    assert summary["silver"] >= 1
    assert summary["gold_detail"]["metrics"] >= 1


def test_lake_cli_show_labels_source_file(demo_rt, monkeypatch):
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
    renewal = _region(demo_rt, "for_renewal", "discount_policy.py")
    sid = demo_rt.new_session(agent="test", condition="traceweaver")
    demo_rt.collector.record(session_id=sid, source="program_trace", operation="test", regions=[renewal])
    demo_rt.collector.record(session_id=sid, source="agent_tool", operation="read", query="renewal", regions=[renewal])
    (demo_rt.cfg.traceweaver_dir / "last_ab.json").write_text(
        json.dumps(
            {
                "fixture": False,
                "baseline": {"repo_tool_calls": 20, "repo_tokens": 200},
                "traceweaver": {"repo_tool_calls": 5, "repo_tokens": 50},
            }
        ),
        encoding="utf-8",
    )
    built = runner.invoke(app, ["lake", "build", "--repo", str(demo_rt.cfg.repo_root)])
    assert built.exit_code == 0, built.stdout
    assert "backend: local-lake" in built.stdout
    shown = runner.invoke(app, ["lake", "show", "--repo", str(demo_rt.cfg.repo_root)])
    assert shown.exit_code == 0, shown.stdout
    assert "backend: local-lake" in shown.stdout
    assert "savings source: last_ab.json" in shown.stdout
    assert "20" in shown.stdout and "5" in shown.stdout


def test_configured_databricks_host_is_not_called(demo_rt, monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.invalid")
    monkeypatch.setenv("DATABRICKS_TOKEN", "not-a-real-token")
    report = build_lake(demo_rt.cfg)
    assert report["backend"] == "local-lake"
    assert report["databricks_called"] is False
    assert "not called" in report["note"].lower() or "was not called" in report["note"]
