"""Real-agent A/B runner using the Claude Code CLI (`claude -p`).

Both conditions run the *same* model, prompt, repo commit, and tool set
(Grep/Glob/Read/Edit/Bash). Differences:

  baseline : hooks installed in observe-only mode (LEDGER_OBSERVE_ONLY=1), no
             MCP server, no policy text. LEDGER records cost but never advises.
  ledger   : hooks advise, `.mcp.json` exposes ledger_* tools, and the agent
             policy is appended to the system prompt.

Success = the task's pytest selection passes after the run (patch tasks) or
the target file appears in the recorded trajectory (localize tasks).
Requires the `claude` CLI and valid credentials; this harness is not run by
the automated tests.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from ledger.adapters.claude_hooks import AGENT_POLICY, install_claude_integration
from ledger.config import LedgerConfig
from ledger.eval.runner import restore_snapshot, snapshot_targets
from ledger.runtime import LedgerRuntime
from ledger.trace.runner import run_pytest_traced


def claude_available() -> bool:
    return shutil.which("claude") is not None


def run_claude_task(cfg: LedgerConfig, task: dict, use_ledger: bool, model: str | None = None, max_turns: int = 30, timeout_s: int = 900) -> dict:
    if not claude_available():
        raise RuntimeError("claude CLI not found on PATH")
    rt = LedgerRuntime(cfg)
    install_claude_integration(cfg)
    condition = "ledger" if use_ledger else "baseline"
    snap = snapshot_targets(cfg.repo_root, task)
    sid = rt.new_session(agent="claude", condition=condition, task_id=task["id"])
    rt.set_objective(sid, task["prompt"])
    # Pre-map the Claude session (unknown until hooks fire) by making this the current session.
    env = os.environ.copy()
    env["LEDGER_REPO"] = str(cfg.repo_root)
    env["LEDGER_OBSERVE_ONLY"] = "0" if use_ledger else "1"

    prompt = task["prompt"]
    if task.get("pytest_args"):
        prompt += f"\n\nRun `python -m pytest {' '.join(a for a in task['pytest_args'] if a != '-q')} -q` to reproduce. "
    prompt += "Fix the code if a test fails; otherwise report the responsible file and function."

    cmd = [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--max-turns", str(max_turns),
        "--allowedTools", "Grep,Glob,Read,Edit,MultiEdit,Write,Bash" + (",mcp__ledger__*" if use_ledger else ""),
        "--permission-mode", "acceptEdits",
    ]
    if model:
        cmd += ["--model", model]
    if use_ledger:
        cmd += ["--mcp-config", str(cfg.repo_root / ".mcp.json"), "--append-system-prompt", AGENT_POLICY]
    else:
        cmd += ["--strict-mcp-config"]  # ignore any project MCP servers in baseline

    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=cfg.repo_root, env=env, capture_output=True, text=True, timeout=timeout_s)
        out = proc.stdout
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
    elapsed = time.time() - t0

    usage = {}
    try:
        payload = json.loads(out) if out.strip().startswith("{") else {}
        usage = {
            "cost_usd": payload.get("total_cost_usd"),
            "duration_ms": payload.get("duration_ms"),
            "num_turns": payload.get("num_turns"),
            "usage": payload.get("usage"),
            "result": (payload.get("result") or "")[:2000],
            "claude_session_id": payload.get("session_id"),
        }
    except json.JSONDecodeError:
        usage = {"raw": out[-2000:]}

    # Attach the Claude session's events to our eval session id if hooks created a separate one.
    ext = usage.get("claude_session_id")
    if ext:
        mapped = cfg.ledger_dir / "sessions" / f"ext-{ext}"
        if mapped.exists():
            hook_sid = mapped.read_text().strip()
            if hook_sid != sid:
                for table in ("events", "working_set", "co_access", "bundles", "controller_log"):
                    rt.conn.execute(f"UPDATE OR IGNORE {table} SET session_id = ? WHERE session_id = ?", (sid, hook_sid))
                rt.conn.execute("DELETE FROM sessions WHERE session_id = ?", (hook_sid,))
                rt.conn.commit()

    # success check
    if task.get("pytest_args"):
        verify = run_pytest_traced(cfg, sid, args=task["pytest_args"], conn=None, collector=None, controller=None, record_event=False)
        success = bool(verify["passed"])
    else:
        success = True
    targets = set(task.get("target_files") or [])
    seen_paths = {
        row["query"] for row in rt.conn.execute(
            "SELECT query FROM events WHERE session_id = ? AND source = 'agent_tool' AND operation IN ('read','edit')", (sid,)
        )
    }
    localized = any(any(t in (p or "") for t in targets) for p in seen_paths)
    if task.get("success", "localize") == "localize":
        success = localized

    rt.collector.record(session_id=sid, source="agent_tool", operation="complete", task_id=task["id"], success=success, extra={"usage": usage})
    rt.end_session(sid, success)
    restore_snapshot(cfg.repo_root, snap)
    rt.sync()
    metrics = rt.controller.metrics(sid)
    metrics.update(
        {
            "task_id": task["id"],
            "family": task.get("family"),
            "condition": condition,
            "agent": "claude",
            "success": success,
            "localized": localized,
            "time_s": round(elapsed, 1),
            "model_usage": usage,
        }
    )
    return metrics
