"""Claude hook JSON in/out, and observe-only vs advice."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from ledger.adapters.claude_hooks import (
    handle_post,
    handle_pre,
    handle_session_start,
    handle_user_prompt,
    install_claude_integration,
)
from ledger.cli import app

runner = CliRunner()


def _grep(session_id: str, pattern: str) -> dict:
    return {"session_id": session_id, "tool_name": "Grep", "tool_input": {"pattern": pattern}}


def test_install_hooks_use_this_interpreter(demo_rt):
    paths = install_claude_integration(demo_rt.cfg)
    settings = Path(paths["settings"]).read_text()
    assert sys.executable in settings
    mcp = json.loads(Path(paths["mcp"]).read_text())
    assert mcp["mcpServers"]["ledger"]["command"] == sys.executable


def test_hook_pre_json_does_not_hang_on_stdin(demo_rt):
    payload = _grep("cli-pre", "for_renewal")
    result = runner.invoke(
        app,
        ["hook", "pre", "--repo", str(demo_rt.cfg.repo_root)],
        input=json.dumps(payload),
    )
    assert result.exit_code == 0


def test_post_read_records_discount_policy(demo_rt):
    payload = {
        "session_id": "hook-read",
        "tool_name": "Read",
        "tool_input": {"file_path": "shop/billing/discount_policy.py"},
        "tool_response": {
            "file": {
                "filePath": "shop/billing/discount_policy.py",
                "content": "def for_renewal():\n    return 0\n",
            }
        },
    }
    assert handle_post(demo_rt, payload) == {}
    sid = demo_rt.ensure_session(agent="claude", condition="ledger", external_id="hook-read")
    events = demo_rt.collector.list_events(sid, 50)
    assert any(e["operation"] == "read" and "discount_policy.py" in (e.get("query") or "") for e in events)


def test_observe_only_second_grep_does_not_advise(demo_rt):
    demo_rt.cfg.observe_only = True
    sid = "observe-grep"
    handle_user_prompt(demo_rt, {"session_id": sid, "prompt": "for_renewal loyalty"})
    first = handle_pre(demo_rt, _grep(sid, "for_renewal"))
    second = handle_pre(demo_rt, _grep(sid, "loyalty"))
    assert first == {}
    assert second == {}
    assert "hookSpecificOutput" not in json.dumps(second)
    demo_rt.cfg.observe_only = False


def test_second_grep_advises_after_traced_pytest(demo_rt):
    demo_rt.cfg.observe_only = False
    sid = "advise-grep"
    start = handle_session_start(demo_rt, {"session_id": sid, "source": "test"})
    assert "ledger_context" in start["hookSpecificOutput"]["additionalContext"]
    handle_user_prompt(
        demo_rt,
        {
            "session_id": sid,
            "prompt": "Renewal invoices ignore loyalty discounts. Find the discount policy.",
        },
    )
    cmd = "python -m pytest -q tests/test_renewal_discount.py"
    handle_post(
        demo_rt,
        {
            "session_id": sid,
            "tool_name": "Bash",
            "tool_input": {"command": cmd},
            "tool_response": {"stdout": "FAILED", "stderr": ""},
        },
    )
    assert handle_pre(demo_rt, _grep(sid, "loyalty")) == {}
    advised = handle_pre(demo_rt, _grep(sid, "renewal"))
    text = advised["hookSpecificOutput"]["additionalContext"]
    assert "discount_policy" in text or "for_renewal" in text
