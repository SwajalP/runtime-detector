from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from ledger import __version__
from ledger.config import LedgerConfig

app = typer.Typer(help="LEDGER Runtime — a context memory hierarchy for coding agents", no_args_is_help=True)
console = Console()

RepoOpt = typer.Option(None, "--repo", help="Repository root (defaults to the nearest .ledger/ or cwd)")


def _cfg(repo: Optional[Path]) -> LedgerConfig:
    return LedgerConfig.from_root(repo) if repo else LedgerConfig.discover()


def _rt(repo: Optional[Path]):
    from ledger.runtime import LedgerRuntime

    return LedgerRuntime(_cfg(repo))


@app.command()
def init(repo: Optional[Path] = RepoOpt, no_claude: bool = typer.Option(False, help="Skip Claude Code hooks/MCP install")):
    """Index the repo, create .ledger/, install Claude Code hooks + MCP config."""
    rt = _rt(repo)
    result = rt.init(install_claude=not no_claude)
    console.print(f"[bold]LEDGER initialized[/bold] in {rt.cfg.repo_root}")
    console.print(f"  index: {result['index']}")
    if result.get("claude"):
        console.print(f"  claude hooks: {result['claude']['settings']}")
        console.print(f"  mcp config:   {result['claude']['mcp']}")
    console.print(f"  db: {result['db']}")


@app.command()
def index(repo: Optional[Path] = RepoOpt, full: bool = typer.Option(False, help="Full rebuild instead of incremental sync")):
    """Incrementally re-index changed files (or --full). Prints signature-card sizes from the index."""
    from ledger.index.cards import format_card_line, measure_cards
    from ledger.index.parser import TREE_SITTER_AVAILABLE

    rt = _rt(repo)
    stats = rt.reindex() if full else rt.sync()
    cards = measure_cards(rt.conn)
    backend = "tree-sitter" if TREE_SITTER_AVAILABLE and rt.cfg.parser_backend != "ast" else "ast"
    console.print(stats)
    console.print(format_card_line(cards, backend=backend))


@app.command()
def serve(repo: Optional[Path] = RepoOpt, host: str = "127.0.0.1", port: Optional[int] = None):
    """Run the metrics API + live dashboard (SSE)."""
    import uvicorn

    from ledger.api.app import create_app

    rt = _rt(repo)
    rt.cfg.ensure_dirs()
    rt.sync()
    port = port or rt.cfg.port
    console.print(f"Dashboard: [cyan]http://{host}:{port}[/cyan]  repo={rt.cfg.repo_root}")
    uvicorn.run(create_app(rt), host=host, port=port, log_level="warning")


@app.command("run")
def run_cmd(
    agent: str = typer.Option("claude", help="claude | simulated"),
    task: Optional[str] = typer.Option(None, help="Task id from tasks.yaml (simulated/claude eval)"),
    repo: Optional[Path] = RepoOpt,
    baseline: bool = typer.Option(False, help="Baseline condition: observe only, no LEDGER tools"),
):
    """Start a session. Claude: installs hooks/MCP and prints how to launch. Simulated: runs one task."""
    from ledger.eval.runner import load_tasks, run_task

    rt = _rt(repo)
    rt.init(install_claude=(agent == "claude"))
    if agent == "claude":
        condition = "baseline" if baseline else "ledger"
        sid = rt.new_session(agent="claude", condition=condition, task_id=task)
        py = sys.executable
        console.print(f"Session [bold]{sid}[/bold] ({condition})")
        console.print(f"Hooks + MCP use this interpreter: [cyan]{py}[/cyan]")
        console.print(f"  hook: {py} -m ledger hook <phase> --repo {rt.cfg.repo_root}")
        console.print(f"  mcp:  {py} -m ledger mcp --repo {rt.cfg.repo_root}")
        console.print("Two conditions (set the env when you launch claude; hooks read it at process start):")
        console.print("  baseline, observe-only, no advice:")
        console.print("    [cyan]LEDGER_OBSERVE_ONLY=1 claude --strict-mcp-config[/cyan]")
        console.print("  ledger, advice + ledger_context / ledger_search / ledger_explain:")
        console.print("    [cyan]claude[/cyan]")
        console.print(f"  policy: {rt.cfg.ledger_dir / 'AGENT_POLICY.md'}")
        console.print("Login-free demo of the same hook schema (does not need the claude binary):")
        console.print(f"  [cyan]ledger ab --task {task or 'renewal-discount'} --repo {rt.cfg.repo_root}[/cyan]")
        console.print(f"Prompt file: docs/prompts/renewal-discount.md")
        console.print(f"Dashboard: [cyan]ledger serve --repo {rt.cfg.repo_root}[/cyan]")
        if baseline:
            console.print("This session is the observe-only baseline. Export LEDGER_OBSERVE_ONLY=1 before claude.")
        return
    tasks = {t["id"]: t for t in load_tasks(rt.cfg.repo_root)}
    task = task or next(iter(tasks))
    if task not in tasks:
        raise typer.BadParameter(f"unknown task {task}; known: {', '.join(tasks)}")
    rt.new_session(agent="simulated", condition="baseline" if baseline else "ledger", task_id=task)
    console.print_json(data=run_task(rt, tasks[task], use_ledger=not baseline))


@app.command("ab")
def ab_cmd(
    task: str = typer.Option("renewal-discount", "--task", help="Task id (default renewal-discount)"),
    repo: Optional[Path] = RepoOpt,
    attempt_claude: bool = typer.Option(
        True,
        "--attempt-claude/--hooks-only",
        help="Probe claude -p. Login failure is recorded and does not replace the hook A/B.",
    ),
):
    """Hook-schema A/B: observe-only baseline vs LEDGER advice. No Claude login required."""
    from ledger.adapters.claude_ab import run_ab

    report = run_ab(_cfg(repo), task_id=task, attempt_claude=attempt_claude)
    _print_ab(report)


@app.command()
def eval(
    repo: Optional[Path] = RepoOpt,
    task: Optional[str] = typer.Option(None, help="Single task id"),
    held_out: bool = typer.Option(False, help="Run only held-out tasks"),
    all_tasks: bool = typer.Option(False, "--all", help="Run dev + held-out tasks"),
    repeats: int = typer.Option(1, min=1),
    agent: str = typer.Option("simulated", help="simulated | claude (needs the claude CLI + credentials)"),
    model: Optional[str] = typer.Option(None, help="Model for --agent claude"),
):
    """A/B: baseline vs LEDGER on the same tasks. Writes .ledger/last_eval.{json,md}."""
    from ledger.eval.runner import compare_task, load_tasks, render_markdown, run_suite

    rt = _rt(repo)
    rt.init(install_claude=(agent == "claude"))
    cfg = rt.cfg
    if agent == "claude":
        from ledger.adapters.claude_runner import claude_available, run_claude_task
        from ledger.eval.metrics import aggregate, per_task_rows
        from ledger.eval.runner import _compare_rows, _suite_change

        if not claude_available():
            raise typer.Exit(code=typer.echo("claude CLI not found on PATH", err=True) or 2)
        tasks = load_tasks(cfg.repo_root)
        if task:
            tasks = [t for t in tasks if t["id"] == task]
        elif not all_tasks:
            tasks = [t for t in tasks if bool(t.get("held_out")) == held_out]
        rows = []
        for t in tasks:
            console.print(f"[dim]claude baseline · {t['id']}[/dim]")
            base = run_claude_task(cfg, t, use_ledger=False, model=model)
            console.print(f"[dim]claude ledger   · {t['id']}[/dim]")
            led = run_claude_task(cfg, t, use_ledger=True, model=model)
            rows.append(_compare_rows(t, base, led))
        suite = {
            "agent": "claude", "model": model, "repeats": 1, "held_out": held_out, "n_tasks": len(tasks), "tasks": rows,
            "per_task": per_task_rows(rows),
            "baseline": aggregate([r["baseline"] for r in rows]), "ledger": aggregate([r["ledger"] for r in rows]),
            "regressions": [r["task_id"] for r in rows if r["regression"]], "config": cfg.to_json(),
        }
        suite["change_pct"] = _suite_change(suite["baseline"], suite["ledger"])
        (cfg.ledger_dir / "last_eval.json").write_text(json.dumps(suite, indent=2, default=str))
        (cfg.ledger_dir / "last_eval.md").write_text(render_markdown(suite))
        _print_suite(suite)
        return

    if task:
        tasks = {t["id"]: t for t in load_tasks(cfg.repo_root)}
        if task not in tasks:
            raise typer.BadParameter(f"unknown task {task}")
        _print_compare(compare_task(cfg, tasks[task]))
        return
    suite = run_suite(cfg, held_out=None if all_tasks else held_out, repeats=repeats)
    _print_suite(suite)
    console.print(f"[dim]written: {cfg.ledger_dir / 'last_eval.json'}  {cfg.ledger_dir / 'last_eval.md'}[/dim]")


@app.command()
def mcp(repo: Optional[Path] = RepoOpt):
    """MCP stdio server exposing ledger_search, ledger_context, ledger_explain."""
    from ledger.mcp.server import serve_stdio

    serve_stdio(_rt(repo))


@app.command()
def hook(
    phase: str = typer.Argument(..., help="session-start | user-prompt | pre | post | stop"),
    repo: Optional[Path] = RepoOpt,
):
    """Claude Code hook entrypoint. Reads the hook JSON payload from stdin."""
    from ledger.adapters import claude_hooks as h

    rt = _rt(repo)
    handlers = {
        "session-start": h.handle_session_start, "SessionStart": h.handle_session_start,
        "user-prompt": h.handle_user_prompt, "UserPromptSubmit": h.handle_user_prompt,
        "pre": h.handle_pre, "PreToolUse": h.handle_pre,
        "post": h.handle_post, "PostToolUse": h.handle_post,
        "stop": h.handle_stop, "Stop": h.handle_stop,
    }
    fn = handlers.get(phase)
    if fn is None:
        raise typer.BadParameter(f"unknown phase {phase}")
    try:
        out = fn(rt)
    except Exception as exc:  # a hook failure must never block the agent
        sys.stderr.write(f"ledger hook error: {exc}\n")
        out = {}
    if out:
        json.dump(out, sys.stdout)
        sys.stdout.write("\n")


@app.command()
def context(
    objective: str,
    seed: Optional[str] = None,
    budget: Optional[int] = None,
    repo: Optional[Path] = RepoOpt,
    text: bool = typer.Option(True, help="Render as text (--no-text for JSON)"),
):
    """Build a context bundle from the command line (same code path as the MCP tool)."""
    from ledger.mcp.tools import render_bundle_text

    rt = _rt(repo)
    rt.sync()
    sid = rt.ensure_session(agent="cli")
    bundle = rt.controller.build_bundle(sid, objective=objective, seed=seed, budget=budget)
    console.print(render_bundle_text(bundle) if text else json.dumps(bundle, indent=2))


@app.command()
def test(
    pytest_args: list[str] = typer.Argument(None, help="Arguments passed to pytest"),
    repo: Optional[Path] = RepoOpt,
):
    """Run pytest under coverage and join the execution trace with the index."""
    from ledger.trace.runner import run_pytest_traced

    rt = _rt(repo)
    rt.sync()
    sid = rt.ensure_session(agent="cli")
    res = run_pytest_traced(rt.cfg, sid, args=pytest_args or ["-q"], conn=rt.conn, collector=rt.collector, controller=rt.controller)
    console.print(res["stdout"][-1500:])
    names = lambda ids: [rt.conn.execute("SELECT symbol FROM source_regions WHERE region_id = ?", (i,)).fetchone()[0] for i in ids]
    console.print(f"[bold]frames:[/bold] {names(res['frame_region_ids'])}")
    console.print(f"[bold]failing-only:[/bold] {names(res['failing_only_region_ids'])}")
    console.print(f"[bold]executed:[/bold] {names(res['executed_region_ids'])}")


@app.command()
def explain(bundle_id: str, repo: Optional[Path] = RepoOpt):
    """Deterministic explanation of a served bundle."""
    rt = _rt(repo)
    res = rt.controller.explain(bundle_id)
    console.print(res.get("text") or res)


@app.command()
def sessions(repo: Optional[Path] = RepoOpt, limit: int = 20):
    """List recorded sessions."""
    rt = _rt(repo)
    table = Table(title="sessions")
    for col in ("session_id", "agent", "condition", "task", "success", "events", "label"):
        table.add_column(col)
    for s in rt.conn.execute("SELECT * FROM sessions ORDER BY started_ms DESC LIMIT ?", (limit,)):
        n = rt.conn.execute("SELECT COUNT(*) c FROM events WHERE session_id = ?", (s["session_id"],)).fetchone()["c"]
        table.add_row(s["session_id"], s["agent"], s["condition"], str(s["task_id"] or ""), str(s["success"]), str(n), str(s["label"] or ""))
    console.print(table)


@app.command()
def export(session_id: str, out: Path, repo: Optional[Path] = RepoOpt):
    """Export a session's event log to JSONL (for replay or sharing)."""
    from ledger.replay import export_session

    n = export_session(_rt(repo), session_id, out)
    console.print(f"exported {n} events → {out}")


@app.command()
def replay(
    src: Optional[Path] = typer.Argument(
        None,
        help="JSONL from `ledger export`. Omit to use fixtures/replay/renewal-discount.jsonl.",
    ),
    speed: float = typer.Option(0.0, help="Inter-event speed. 0 = as-fast-as-possible (venue default)."),
    repo: Optional[Path] = RepoOpt,
):
    """Replay a recorded session into a new session labelled REPLAY (not a live model)."""
    from ledger.replay import default_fixture, replay_session

    path = src or default_fixture()
    if not path.exists():
        raise typer.BadParameter(f"replay fixture not found: {path}")
    sid = replay_session(_rt(repo), path, speed=speed)
    console.print(f"replayed into session {sid} (labelled REPLAY — not a live model)")


@app.command("delete-session")
def delete_session(session_id: str, repo: Optional[Path] = RepoOpt):
    """Delete all stored data for a session (privacy)."""
    _rt(repo).delete_session(session_id)
    console.print(f"deleted {session_id}")


@app.command()
def agentverse(
    repo: Optional[Path] = RepoOpt,
    local: bool = typer.Option(True, "--local/--mailbox", help="Bind localhost, or register an Agentverse mailbox"),
    port: int = typer.Option(8000, help="Local HTTP port when --local"),
    seed: Optional[str] = typer.Option(None, help="Deterministic agent seed"),
):
    """Run the Fetch.ai uAgent exposing LedgerContextProtocol + chat routing."""
    from ledger.agentverse.agent import run_agent

    run_agent(repo=repo, local=local, port=port, seed=seed)


@app.command("agentverse-ask")
def agentverse_ask(
    objective: str = typer.Argument(..., help="Natural-language objective or chat utterance"),
    port: int = typer.Option(8000, help="Port of a running local agent"),
    chat: bool = typer.Option(False, "--chat", help="Send ChatText instead of ContextRequest"),
    endpoint: Optional[str] = typer.Option(None, help="Override the /submit URL"),
):
    """Ask a locally running ledger-runtime agent. Uses the committed demo identity."""
    import asyncio

    from ledger.agentverse.client import ask, format_reply, reply_ok
    from ledger.agentverse.identity import LOCAL_IDENTITY_LABEL, demo_address

    address = demo_address()
    url = endpoint or f"http://127.0.0.1:{port}/submit"
    console.print(f"{LOCAL_IDENTITY_LABEL}: {address}")
    result = asyncio.run(ask(address, objective, url, chat=chat))
    text = format_reply(result)
    console.print(text)
    if not reply_ok(text):
        raise typer.Exit(code=1)


@app.command()
def integrate(
    target: str = typer.Argument(..., help="claude | cline | gpt"),
    repo: Optional[Path] = RepoOpt,
):
    """Write MCP/tool config for Claude Code, Cline, or Codex (GPT)."""
    from ledger.adapters.integrate import integrate as integrate_target

    try:
        result = integrate_target(_cfg(repo), target)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print_json(data=result)


@app.command("gpt-turn")
def gpt_turn_cmd(
    objective: str = typer.Option(..., "--objective", help="What the turn should answer"),
    seed: Optional[str] = typer.Option(None, "--seed", help="Symbol or test seed for the bundle"),
    repo: Optional[Path] = RepoOpt,
):
    """One turn: OpenAI if OPENAI_API_KEY is set, otherwise a local backup.

    LEDGER_DUMMY=1 prints the labeled fixture in ledger/fixtures/gpt_trace.json.
    Fixtures are never reported as live Claude or GPT measurements.
    """
    from ledger.adapters.gpt_turn import gpt_turn

    payload = gpt_turn(_cfg(repo), objective, seed=seed)
    sys.stdout.write(json.dumps(payload, indent=2) + "\n")


@app.command()
def audit(repo: Optional[Path] = RepoOpt):
    """Structural audit: SCCs, min-cut, clone clusters, lexical traps. Writes last_audit.json."""
    from ledger.audit import write_audit

    report = write_audit(_cfg(repo))
    summary = {
        "written": report.get("written"),
        "regions": report.get("regions"),
        "directed_edges": report.get("directed_edges"),
        "resolved_calls": report.get("resolved_calls"),
        "unresolved_calls": report.get("unresolved_calls"),
        "reverse_bfs": report.get("reverse_bfs"),
        "large_sccs": report.get("large_sccs"),
        "largest_scc_size": report.get("largest_scc_size"),
        "min_cut": report.get("min_cut"),
        "clone_clusters": len(report.get("clone_clusters") or []),
        "lexical_traps": len(report.get("lexical_traps") or []),
        "disclaimer": report.get("disclaimer"),
        "fixture_comparison_labeled": bool((report.get("fixture_comparison") or {}).get("fixture")),
    }
    console.print_json(data=summary)


@app.command()
def version():
    """Print the installed LEDGER version."""
    console.print(__version__)


# --------------------------------------------------------------------------- #


def _print_compare(result: dict) -> None:
    table = Table(title=f"{result['task_id']}  (success held: {result['success_held']})")
    for col in ("metric", "baseline", "LEDGER", "change"):
        table.add_column(col)
    b, l, c = result["baseline"], result["ledger"], result["change_pct"]
    table.add_row("success", str(b.get("success")), str(l.get("success")), "equal" if bool(b.get("success")) == bool(l.get("success")) else "DIFF")
    table.add_row("repo search/read calls", str(b.get("repo_tool_calls")), str(l.get("repo_tool_calls")), _pct(c.get("repo_tool_calls")))
    table.add_row("total tool calls (incl. LEDGER)", str(b.get("total_tool_calls")), str(l.get("total_tool_calls")), _pct(c.get("total_tool_calls")))
    table.add_row("repo tokens (incl. injected)", str(b.get("repo_tokens")), str(l.get("repo_tokens")), _pct(c.get("repo_tokens")))
    table.add_row("time to target (s)", str(b.get("time_to_target_s")), str(l.get("time_to_target_s")), _pct(c.get("time_to_target_s")))
    table.add_row("calls to target", str(b.get("calls_to_target")), str(l.get("calls_to_target")), _pct(c.get("calls_to_target")))
    table.add_row("hit rate", str(b.get("hit_rate")), str(l.get("hit_rate")), "")
    table.add_row("prefetch precision", "—", str(l.get("prefetch_precision")), "")
    table.add_row("stale served", str(b.get("stale_served")), str(l.get("stale_served")), "")
    console.print(table)


def _print_suite(suite: dict) -> None:
    b, l, c = suite["baseline"], suite["ledger"], suite["change_pct"]
    table = Table(title=f"LEDGER vs baseline · {suite['n_tasks']} tasks × {suite.get('repeats', 1)} · agent={suite.get('agent')}")
    for col in ("condition", "success", "repo calls", "total calls", "repo tokens", "median t→target", "mean calls→target"):
        table.add_column(col)
    table.add_row("Baseline", f"{b['success']}/{b['n']}", str(b["repo_tool_calls"]), str(b["total_tool_calls"]), f"{b['repo_tokens']:,}", str(b["median_time_to_target_s"]), str(b["mean_calls_to_target"]))
    table.add_row("LEDGER", f"{l['success']}/{l['n']}", str(l["repo_tool_calls"]), str(l["total_tool_calls"]), f"{l['repo_tokens']:,}", str(l["median_time_to_target_s"]), str(l["mean_calls_to_target"]))
    table.add_row("Change", "equal" if b["success"] == l["success"] else "[red]DIFF[/red]", _pct(c["repo_tool_calls"]), _pct(c["total_tool_calls"]), _pct(c["repo_tokens"]), _pct(c["median_time_to_target_s"]), _pct(c["mean_calls_to_target"]))
    console.print(table)
    console.print(
        f"controller: hit rate {l.get('hit_rate')} · prefetch precision {l.get('prefetch_precision')} · "
        f"pollution {l.get('pollution_rate')} · stale served {l.get('stale_served')} · fallback {l.get('fallback_rate')}"
    )
    per = Table(title="per task")
    for col in ("task", "family", "base ✓", "LEDGER ✓", "base calls", "LEDGER calls", "base tokens", "LEDGER tokens", "Δ tokens"):
        per.add_column(col)
    for r in suite["per_task"]:
        per.add_row(
            r["task_id"], str(r["family"]), "✓" if r["baseline_success"] else "✗", "✓" if r["ledger_success"] else "✗",
            str(r["baseline_calls"]), str(r["ledger_calls"]), f"{r['baseline_tokens']:,}", f"{r['ledger_tokens']:,}", _pct(r["tokens_change_pct"]),
        )
    console.print(per)
    if suite.get("regressions"):
        console.print(f"[red]regressions:[/red] {suite['regressions']}")


def _print_ab(report: dict) -> None:
    table = Table(title=f"hook A/B · {report['task_id']} · source={report['source']}")
    for col in ("condition", "target found", "advice", "repo calls", "ledger calls", "repo tokens"):
        table.add_column(col)
    for key in ("baseline", "ledger"):
        row = report[key]
        table.add_row(
            row["condition"],
            "yes" if row["target_found"] else "no",
            str(row["advice_count"]),
            str(row["repo_tool_calls"]),
            str(row["ledger_tool_calls"]),
            str(row["repo_tokens"]),
        )
    console.print(table)
    claude = report.get("claude") or {}
    if claude.get("ran"):
        console.print("[green]claude live run stored separately (source=claude)[/green]")
    else:
        console.print(f"claude live: not run — {claude.get('reason')}")
    console.print(report.get("note", ""))
    console.print(f"[dim]written: {report.get('written')}[/dim]")


def _pct(v) -> str:
    return "—" if v is None else f"{v:+.1f}%"


if __name__ == "__main__":
    app()
