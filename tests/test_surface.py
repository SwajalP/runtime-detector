"""Index cards, integrate configs, gpt-turn, and the structural audit."""

import json

from typer.testing import CliRunner

from ledger.cli import app
from ledger.index.cards import measure_cards

runner = CliRunner()


def test_index_prints_measured_card_bytes(demo_rt):
    cards = measure_cards(demo_rt.conn)
    stored = demo_rt.conn.execute(
        "SELECT COALESCE(SUM(token_count), 0) t FROM source_regions WHERE kind != 'module'"
    ).fetchone()["t"]
    assert cards["regions"] > 0
    assert cards["full_body_bytes"] > 0
    assert cards["card_bytes"] > 0
    assert cards["full_body_bytes"] > cards["card_bytes"]
    assert cards["full_body_tokens"] == stored
    assert cards["full_body_tokens"] > cards["card_tokens"]
    result = runner.invoke(app, ["index", "--repo", str(demo_rt.cfg.repo_root)])
    assert result.exit_code == 0, result.stdout
    assert "full-body bytes=" + str(cards["full_body_bytes"]) in result.stdout
    assert "card bytes=" + str(cards["card_bytes"]) in result.stdout
    assert "estimated tokens" in result.stdout
    assert "tree-sitter" in result.stdout


def test_integrate_writes_mcp_configs(demo_rt):
    root = demo_rt.cfg.repo_root
    for target in ("claude", "cline", "gpt"):
        result = runner.invoke(app, ["integrate", target, "--repo", str(root)])
        assert result.exit_code == 0, result.stdout + result.stderr
    mcp = json.loads((root / ".mcp.json").read_text())
    assert "ledger" in mcp["mcpServers"]
    assert "mcp" in mcp["mcpServers"]["ledger"]["args"]
    hooks = json.loads((root / ".claude" / "settings.json").read_text())
    assert "PreToolUse" in hooks["hooks"]
    cline = json.loads((root / ".cline" / "mcp_settings.json").read_text())
    assert cline["mcpServers"]["ledger"]["args"][0:3] == ["-m", "ledger", "mcp"]
    assert (root / ".clinerules").is_file()
    toml = (root / ".codex" / "config.toml").read_text()
    assert "[mcp_servers.ledger]" in toml
    assert '"-m"' in toml or "-m" in toml
    tools = json.loads((root / ".gpt" / "tools.json").read_text())
    names = [item["function"]["name"] for item in tools["tools"]]
    assert names == ["ledger_search", "ledger_context", "ledger_explain"]


def test_gpt_turn_dummy_is_labeled_fixture(demo_rt, monkeypatch):
    monkeypatch.setenv("LEDGER_DUMMY", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = runner.invoke(
        app,
        ["gpt-turn", "--objective", "find renewal loyalty", "--repo", str(demo_rt.cfg.repo_root)],
    )
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["fixture"] is True
    assert data["live"] is False
    assert data["provider"] == "fixture"
    assert data["source"] == "fixture"
    assert "not a live" in data["label"].lower()
    assert data.get("model") is None
    saved = json.loads((demo_rt.cfg.ledger_dir / "last_gpt_turn.json").read_text())
    assert saved["fixture"] is True


def test_gpt_turn_without_key_is_local_backup(demo_rt, monkeypatch):
    monkeypatch.delenv("LEDGER_DUMMY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = runner.invoke(
        app,
        [
            "gpt-turn",
            "--objective",
            "renewal invoices ignore loyalty discounts",
            "--seed",
            "for_renewal",
            "--repo",
            str(demo_rt.cfg.repo_root),
        ],
    )
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["fixture"] is False
    assert data["live"] is False
    assert data["source"] == "local_backup"
    assert data["provider"] == "local"
    assert "not a live GPT" in data["label"]
    symbols = [row.get("symbol") for row in data["regions"]]
    assert "for_renewal" in symbols


def test_audit_writes_structural_json(demo_rt):
    result = runner.invoke(app, ["audit", "--repo", str(demo_rt.cfg.repo_root)])
    assert result.exit_code == 0, result.stdout
    path = demo_rt.cfg.ledger_dir / "last_audit.json"
    data = json.loads(path.read_text())
    assert data["fixture"] is False
    assert data["kind"] == "structural"
    assert "large_sccs" in data
    assert "min_cut" in data
    assert "clone_clusters" in data
    assert "lexical_traps" in data
    assert data["min_cut"]["around"].endswith("discount_policy.py::for_renewal")
    assert data["min_cut"]["weight"] is not None
    assert data["min_cut"]["weight"] >= 1
    assert data["min_cut"]["seed_side"]
    assert any(item.endswith("::for_renewal") for item in data["min_cut"]["seed_side"])
    assert "seed-side partition" in data["min_cut"]["note"]
    bfs = data["reverse_bfs"]
    assert bfs["reaches_failing_test"] is True
    assert bfs["depth"] is not None and bfs["depth"] >= 2
    assert bfs["path"][0].endswith("::for_renewal")
    assert bfs["path"][-1].endswith("::test_renewal_applies_loyalty_on_annual_boundary")
    assert data["largest_scc_size"] >= 2
    assert data["large_sccs"]
    assert data["large_sccs"][0]["size"] >= 2
    assert data["directed_edges"] >= 8
    assert any(item["path"].startswith("shop/legacy/") or item["path"].startswith("shop/promotions/") for item in data["lexical_traps"])
    low = data["disclaimer"].lower()
    assert "sonarqube" in low
    assert "not a sonarqube" in low
    assert data["fixture_comparison"]["fixture"] is True
    assert data["mailbox_status"]["fixture"] is True
    assert "beat" in low


def test_help_lists_new_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in ("index", "integrate", "gpt-turn", "audit", "ab"):
        assert name in result.stdout
