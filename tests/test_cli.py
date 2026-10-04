import json
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from traceweaver.cli import app

runner = CliRunner()


def test_cli_help_lists_required_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in (
        "eval", "mcp", "hook", "agentverse", "agentverse-ask", "agentverse-demo",
        "lake", "ab", "serve", "context", "replay",
    ):
        assert name in result.stdout


def test_replay_help_says_not_live():
    result = runner.invoke(app, ["replay", "--help"])
    assert result.exit_code == 0
    assert "REPLAY" in result.stdout
    assert "fixture" in result.stdout.lower()


def test_eval_help_mentions_repo():
    result = runner.invoke(app, ["eval", "--help"])
    assert result.exit_code == 0
    assert "--repo" in result.stdout


def test_mcp_help_mentions_repo():
    result = runner.invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "--repo" in result.stdout


def test_hook_unknown_phase_is_rejected():
    result = runner.invoke(app, ["hook", "not-a-phase"])
    assert result.exit_code != 0


def test_hook_stop_reads_stdin(demo_rt):
    result = runner.invoke(
        app,
        ["hook", "stop", "--repo", str(demo_rt.cfg.repo_root)],
        input=json.dumps({"session_id": "cli-hook-test"}) + "\n",
    )
    assert result.exit_code == 0


def test_python_m_traceweaver_help():
    proc = subprocess.run(
        [sys.executable, "-m", "traceweaver", "--help"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert proc.returncode == 0
    assert "eval" in proc.stdout
    assert "mcp" in proc.stdout
