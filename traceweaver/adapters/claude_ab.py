"""Login-free A/B through the Claude hook and MCP schema.

``traceweaver ab`` does not call the ``claude`` binary. It drives the same
handlers Claude Code would:

* baseline — ``observe_only`` (raw Grep/Read/Bash recorded, no advice, no MCP)
* traceweaver — hooks may advise, then ``traceweaver_context`` / ``traceweaver_search``

A live ``claude -p`` run is attempted only when requested and is stored
separately. A login failure is recorded as not run. It is never filled in
with hook-driver numbers.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from traceweaver.adapters import claude_hooks as hooks
from traceweaver.config import TraceWeaverConfig
from traceweaver.eval.runner import load_tasks, restore_snapshot, snapshot_targets
from traceweaver.events.schema import new_id
from traceweaver.mcp.server import handle as mcp_handle
from traceweaver.runtime import TraceWeaverRuntime

_LOGIN_MARKERS = ("Invalid API key", "Please run /login", "not logged in", "Not logged in")


def run_ab(
    cfg: TraceWeaverConfig,
    task_id: str = "renewal-discount",
    *,
    attempt_claude: bool = False,
    claude_timeout_s: int = 20,
) -> dict:
    """Reset task files, run both hook conditions, write ``.traceweaver/last_ab.json``."""
    tasks = {t["id"]: t for t in load_tasks(cfg.repo_root)}
    if task_id not in tasks:
        known = ", ".join(sorted(tasks))
        raise ValueError(f"unknown task {task_id}; known: {known}")
    task = tasks[task_id]
    cfg.ensure_dirs()
    rt = TraceWeaverRuntime(cfg)
    rt.sync()

    _reset_tracked_files(cfg.repo_root, task)
    snap = snapshot_targets(cfg.repo_root, task)
    try:
        restore_snapshot(cfg.repo_root, snap)
        baseline = _run_condition(cfg, task, observe_only=True)
        restore_snapshot(cfg.repo_root, snap)
        traceweaver = _run_condition(cfg, task, observe_only=False)
    finally:
        restore_snapshot(cfg.repo_root, snap)

    claude = (
        _attempt_claude(cfg, task, timeout_s=claude_timeout_s)
        if attempt_claude
        else {"ran": False, "reason": "not attempted", "source": "claude"}
    )
    report = {
        "source": "claude_hooks",
        "agent": "hook-driver",
        "task_id": task["id"],
        "repo": str(cfg.repo_root),
        "note": (
            "Hook-schema A/B (observe-only baseline vs advise + traceweaver_context/traceweaver_search). "
            "Not a live Claude model run unless claude.ran is true."
        ),
        "baseline": baseline,
        "traceweaver": traceweaver,
        "claude": claude,
    }
    out = cfg.traceweaver_dir / "last_ab.json"
    out.write_text(json.dumps(report, indent=2, default=str) + "\n")
    report["written"] = str(out)
    return report


def _run_condition(cfg: TraceWeaverConfig, task: dict, *, observe_only: bool) -> dict:
    cfg.observe_only = observe_only
    rt = TraceWeaverRuntime(cfg)
    rt.sync()
    ext = f"ab-{'baseline' if observe_only else 'traceweaver'}-{new_id('run')}"
    prompt = str(task.get("prompt") or "").strip()
    targets = list(task.get("target_files") or [])
    advice: list[dict] = []

    def pre(payload: dict) -> dict:
        out = hooks.handle_pre(rt, payload)
        if out.get("hookSpecificOutput"):
            advice.append(out)
        return out

    started = hooks.handle_session_start(rt, {"session_id": ext, "source": "hook-driver"})
    session_advice = bool(started.get("hookSpecificOutput"))
    hooks.handle_user_prompt(rt, {"session_id": ext, "prompt": prompt})

    pytest_cmd = "python -m pytest " + " ".join(task.get("pytest_args") or ["-q"])
    hooks.handle_pre(rt, {"session_id": ext, "tool_name": "Bash", "tool_input": {"command": pytest_cmd}})
    hooks.handle_post(
        rt,
        {
            "session_id": ext,
            "tool_name": "Bash",
            "tool_input": {"command": pytest_cmd},
            "tool_response": {"stdout": "", "stderr": ""},
        },
    )

    queries = list(task.get("baseline_queries") or ["renewal"])
    grep_files: list[str] = []
    if observe_only:
        for query in queries:
            pre({"session_id": ext, "tool_name": "Grep", "tool_input": {"pattern": query}})
            text, files = _grep_output(cfg, query)
            grep_files.extend(files)
            hooks.handle_post(
                rt,
                {
                    "session_id": ext,
                    "tool_name": "Grep",
                    "tool_input": {"pattern": query},
                    "tool_response": text,
                },
            )
        seen_reads: set[str] = set()
        for rel in grep_files:
            if not rel.endswith(".py") or rel in seen_reads:
                continue
            seen_reads.add(rel)
            body = (cfg.repo_root / rel).read_text(encoding="utf-8", errors="replace")
            hooks.handle_pre(rt, {"session_id": ext, "tool_name": "Read", "tool_input": {"file_path": rel}})
            hooks.handle_post(
                rt,
                {
                    "session_id": ext,
                    "tool_name": "Read",
                    "tool_input": {"file_path": rel},
                    "tool_response": {"file": {"filePath": rel, "content": body[:8000]}},
                },
            )
            if len(seen_reads) >= 6:
                break
        context_entries: list[dict] = []
    else:
        # First broad grep is silent. The second is where Claude hooks advise.
        for query in queries[:2]:
            pre({"session_id": ext, "tool_name": "Grep", "tool_input": {"pattern": query}})
            text, files = _grep_output(cfg, query)
            grep_files.extend(files)
            hooks.handle_post(
                rt,
                {
                    "session_id": ext,
                    "tool_name": "Grep",
                    "tool_input": {"pattern": query},
                    "tool_response": text,
                },
            )
        context_msg = mcp_handle(
            rt,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "traceweaver_context",
                    "arguments": {"objective": prompt, "seed": task.get("seed")},
                },
            },
            session_hint=ext,
        )
        search_msg = mcp_handle(
            rt,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "traceweaver_search",
                    "arguments": {"query": queries[0]},
                },
            },
            session_hint=ext,
        )
        context_entries = ((context_msg or {}).get("result") or {}).get("structuredContent", {}).get("entries") or []
        search_regions = ((search_msg or {}).get("result") or {}).get("structuredContent", {}).get("regions") or []
        context_entries = list(context_entries) + [
            {"path": r.get("path"), "symbol": r.get("symbol")} for r in search_regions
        ]

    sid = rt.ensure_session(
        agent="claude",
        condition="baseline" if observe_only else "traceweaver",
        task_id=task["id"],
        external_id=ext,
    )
    paths = _paths_seen(rt, sid)
    paths.update(p for p in grep_files)
    paths.update(str(e.get("path")) for e in context_entries if e.get("path"))
    target_found = any(t in paths for t in targets)
    metrics = rt.controller.metrics(sid)
    symbols = sorted({str(e.get("symbol")) for e in context_entries if e.get("symbol")})
    return {
        "condition": "baseline" if observe_only else "traceweaver",
        "observe_only": observe_only,
        "session_id": sid,
        "source": "claude_hooks",
        "target_files": targets,
        "target_found": target_found,
        "paths_seen": sorted(paths),
        "advice_count": len(advice),
        "session_advice": session_advice,
        "traceweaver_symbols": symbols,
        "repo_tool_calls": metrics.get("repo_tool_calls"),
        "traceweaver_tool_calls": metrics.get("traceweaver_tool_calls"),
        "total_tool_calls": metrics.get("total_tool_calls"),
        "repo_tokens": metrics.get("repo_tokens"),
        "prefetch_precision": metrics.get("prefetch_precision"),
        "prefetches": metrics.get("prefetches"),
        "stale_served": metrics.get("stale_served"),
    }


def _claude_failure_reason(blob: str) -> str:
    for line in blob.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        result = str(payload.get("result") or "").strip()
        if result:
            return result[:240]
    for marker in _LOGIN_MARKERS:
        if marker in blob:
            return f"claude is not logged in ({marker}). Hook A/B still ran."
    return "claude did not return a non-interactive reply. Hook A/B still ran."


def _attempt_claude(cfg: TraceWeaverConfig, task: dict, *, timeout_s: int) -> dict:
    if shutil.which("claude") is None:
        return {"ran": False, "source": "claude", "reason": "claude CLI not on PATH"}
    try:
        proc = subprocess.run(
            ["claude", "-p", "Reply with exactly: pong", "--output-format", "json", "--max-turns", "1"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {"ran": False, "source": "claude", "reason": "claude probe timed out before a non-interactive reply"}
    blob = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0 or any(m in blob for m in _LOGIN_MARKERS) or "pong" not in blob.lower():
        return {"ran": False, "source": "claude", "reason": _claude_failure_reason(blob)}
    from traceweaver.adapters.claude_runner import run_claude_task

    metrics = run_claude_task(cfg, task, use_traceweaver=True, max_turns=8, timeout_s=180)
    metrics["ran"] = True
    metrics["source"] = "claude"
    return metrics


def _grep_output(cfg: TraceWeaverConfig, pattern: str) -> tuple[str, list[str]]:
    try:
        rx = re.compile(re.escape(pattern), re.I)
    except re.error:
        rx = re.compile(re.escape(pattern), re.I)
    files: list[str] = []
    lines: list[str] = []
    root = cfg.repo_root
    for path in sorted(root.rglob("*")):
        if not path.is_file() or cfg.is_excluded(path) or path.suffix not in {".py", ".md"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(root))
        hit = False
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                lines.append(f"{rel}:{i}:{line.strip()[:160]}")
                hit = True
        if hit:
            files.append(rel)
    return "\n".join(lines), files


def _paths_seen(rt: TraceWeaverRuntime, session_id: str) -> set[str]:
    found: set[str] = set()
    rows = rt.conn.execute(
        "SELECT query, regions_json FROM events WHERE session_id = ?",
        (session_id,),
    ).fetchall()
    for row in rows:
        query = row["query"] or ""
        if query.endswith(".py"):
            found.add(query)
        try:
            regions = json.loads(row["regions_json"] or "[]")
        except json.JSONDecodeError:
            regions = []
        for rid in regions:
            hit = rt.conn.execute("SELECT path FROM source_regions WHERE region_id = ?", (rid,)).fetchone()
            if hit and hit["path"]:
                found.add(hit["path"])
    return found


def _reset_tracked_files(repo: Path, task: dict) -> None:
    """Restore committed task files when this repo is inside a git work tree."""
    rels: list[str] = list(task.get("target_files") or [])
    patch = task.get("patch") or {}
    if patch.get("path"):
        rels.append(patch["path"])
    rels = list(dict.fromkeys(rels))
    if not rels:
        return
    top = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if top.returncode != 0:
        return
    toplevel = Path(top.stdout.strip())
    try:
        prefix = repo.resolve().relative_to(toplevel.resolve())
    except ValueError:
        return
    specs = [str(prefix / rel) if str(prefix) != "." else rel for rel in rels]
    subprocess.run(["git", "-C", str(toplevel), "checkout", "--", *specs], capture_output=True, text=True, check=False)
