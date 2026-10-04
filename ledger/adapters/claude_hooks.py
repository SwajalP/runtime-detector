"""Claude Code adapter: hooks (observation path) + MCP config (optimized path).

Observation path
    SessionStart      → open/attach a LEDGER session, tell the agent LEDGER is on
    UserPromptSubmit  → record the objective (intent for scoring)
    PreToolUse        → record the call; on a *repeated* broad Grep/Glob attach
                        guidance pointing at a prepared bundle (advisory only)
    PostToolUse       → record cost (bytes/tokens), map results to regions,
                        invalidate on Edit/Write, re-run `pytest` under coverage
                        when the agent ran tests via Bash (dual trace join)
    Stop              → close the session

Optimized path
    `.mcp.json` registers `ledger mcp`, exposing ledger_search / ledger_context /
    ledger_explain as first-class tools. Raw Grep/Read are never blocked.

Set LEDGER_OBSERVE_ONLY=1 (or cfg.observe_only) for a baseline run that records
everything but never advises — identical agent, identical tools, no LEDGER help.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from ledger.config import LedgerConfig
from ledger.events.redact import redact_text, should_redact_path
from ledger.events.schema import estimate_tokens
from ledger.index.lexical import grep_regions
from ledger.index.symbols import region_covering, regions_for_path, regions_in_range
from ledger.policy.invalidation import invalidate_path
from ledger.trace.runner import parse_frames_from_text, pytest_args_from_command, run_pytest_traced

TOOL_MATCHER = "Grep|Glob|Read|Edit|MultiEdit|Write|Bash|NotebookEdit|mcp__ledger__.*"


def _hook_cmd(cfg: LedgerConfig, phase: str) -> str:
    py = shlex.quote(sys.executable)
    return f"{py} -m ledger hook {phase} --repo {shlex.quote(str(cfg.repo_root))}"


def hook_settings(cfg: LedgerConfig) -> dict:
    def entry(phase: str, matcher: str | None = None) -> dict:
        d: dict[str, Any] = {"hooks": [{"type": "command", "command": _hook_cmd(cfg, phase), "timeout": 60}]}
        if matcher:
            d["matcher"] = matcher
        return d

    return {
        "hooks": {
            "SessionStart": [entry("session-start")],
            "UserPromptSubmit": [entry("user-prompt")],
            "PreToolUse": [entry("pre", TOOL_MATCHER)],
            "PostToolUse": [entry("post", TOOL_MATCHER)],
            "Stop": [entry("stop")],
        }
    }


def mcp_config(cfg: LedgerConfig) -> dict:
    return {
        "mcpServers": {
            "ledger": {
                "command": sys.executable,
                "args": ["-m", "ledger", "mcp", "--repo", str(cfg.repo_root)],
                "env": {"LEDGER_REPO": str(cfg.repo_root)},
            }
        }
    }


def install_claude_integration(cfg: LedgerConfig) -> dict[str, str]:
    claude_dir = cfg.repo_root / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_path = claude_dir / "settings.json"
    existing = _load_json(settings_path)
    hooks = existing.get("hooks", {})
    hooks.update(hook_settings(cfg)["hooks"])
    existing["hooks"] = hooks
    settings_path.write_text(json.dumps(existing, indent=2) + "\n")

    mcp_path = cfg.repo_root / ".mcp.json"
    mcp_existing = _load_json(mcp_path)
    servers = mcp_existing.get("mcpServers", {})
    servers.update(mcp_config(cfg)["mcpServers"])
    mcp_existing["mcpServers"] = servers
    mcp_path.write_text(json.dumps(mcp_existing, indent=2) + "\n")

    policy_path = cfg.ledger_dir / "AGENT_POLICY.md"
    policy_path.write_text(AGENT_POLICY)
    (cfg.ledger_dir / "claude-hooks-installed").write_text("ok\n")
    return {"settings": str(settings_path), "mcp": str(mcp_path), "policy": str(policy_path)}


AGENT_POLICY = """# LEDGER agent policy (append to the system prompt for the LEDGER condition)

You have LEDGER context tools in addition to your normal tools.

- After the first repository-wide search, or immediately after a failing test,
  call `ledger_context(objective=<the task>, seed=<best symbol, test or error>)`.
- Before a *second* repository-wide Grep/Glob, call `ledger_context` or
  `ledger_search` first.
- Bundles cite exact file:line ranges and content hashes. Trust exact entries;
  open signature/summary entries with Read only if you need their bodies.
- Fall back to raw Grep/Read whenever confidence is low or the bundle is
  insufficient. LEDGER is an accelerator, not an authority.
- `ledger_explain(bundle_id)` shows why each region was included.
"""


# --------------------------------------------------------------------------- #
# hook handlers
# --------------------------------------------------------------------------- #


def _read_stdin() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _session(runtime, payload: dict) -> str:
    ext = payload.get("session_id")
    condition = "baseline" if runtime.cfg.observe_only else "ledger"
    return runtime.ensure_session(agent="claude", condition=condition, external_id=ext)


def handle_session_start(runtime, payload: dict | None = None) -> dict:
    payload = payload if payload is not None else _read_stdin()
    sid = _session(runtime, payload)
    runtime.sync()
    runtime.collector.record(session_id=sid, source="controller", operation="prompt", extra={"hook": "SessionStart", "source": payload.get("source")})
    if runtime.cfg.observe_only:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                "LEDGER Runtime is observing this session (session "
                f"{sid}). Tools: ledger_context(objective, seed), ledger_search(query), ledger_explain(bundle_id). "
                "Call ledger_context after your first search or a failing test, and before repeating a broad Grep. "
                "Raw Grep/Read stay available."
            ),
        }
    }


def handle_user_prompt(runtime, payload: dict | None = None) -> dict:
    payload = payload if payload is not None else _read_stdin()
    sid = _session(runtime, payload)
    prompt = str(payload.get("prompt") or "")[:2000]
    if prompt:
        runtime.set_objective(sid, prompt)
    runtime.collector.record(
        session_id=sid, source="agent_tool", operation="prompt", query=prompt[:500],
        tokens_returned=estimate_tokens(prompt), extra={"hook": "UserPromptSubmit"}, bump=True,
    )
    return {}


def handle_pre(runtime, payload: dict | None = None) -> dict:
    payload = payload if payload is not None else _read_stdin()
    tool = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    sid = _session(runtime, payload)
    op = _op(tool)
    query = _query_from_tool(tool, tool_input)

    runtime.collector.record(
        session_id=sid, source="agent_tool", operation=op, query=query,
        extra={"phase": "pre", "tool": tool, "input_keys": sorted(tool_input)}, bump=True,
    )
    if runtime.cfg.observe_only or op not in {"grep", "glob"} or not query:
        return {}

    prior = runtime.conn.execute(
        "SELECT COUNT(*) c FROM events WHERE session_id = ? AND source = 'agent_tool' AND operation IN ('grep','glob') AND json_extract(extra_json, '$.phase') = 'pre'",
        (sid,),
    ).fetchone()["c"]
    if prior <= 1:  # this call is the first broad search: let it through silently
        return {}

    objective = runtime.objective(sid) or query
    bundle = runtime.controller.build_bundle(sid, objective=objective, seed=query)
    runtime.collector.record(
        session_id=sid, source="controller", operation="hit" if bundle["entries"] else "miss", query=query,
        regions=[e["region_id"] for e in bundle["entries"]], extra={"reason": "repeated_broad_search", "bundle_id": bundle["bundle_id"]},
    )
    if not bundle["entries"]:
        return {}
    lines = [
        f"LEDGER: this is repository-wide search #{prior}. A prepared context bundle {bundle['bundle_id']} "
        f"({bundle['token_count']} tokens, {len(bundle['entries'])} regions) is available via ledger_context / ledger_explain.",
        "Top regions:",
    ]
    for e in bundle["entries"][:4]:
        lines.append(f"- {e['symbol']}  {e['path']}:{e['start_line']}-{e['end_line']}  score={e['score']}  ({e['why']})")
    lines.append("Raw search remains allowed if the bundle looks insufficient.")
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "\n".join(lines)}}


def handle_post(runtime, payload: dict | None = None) -> dict:
    payload = payload if payload is not None else _read_stdin()
    tool = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    response = payload.get("tool_response")
    sid = _session(runtime, payload)
    op = _op(tool)
    query = _query_from_tool(tool, tool_input)

    text = _response_text(response)
    path_arg = str(tool_input.get("file_path") or tool_input.get("path") or "")
    if path_arg and should_redact_path(path_arg, runtime.cfg):
        text = "[redacted]"
    else:
        text = redact_text(text[:60000])

    if tool.startswith("mcp__ledger__"):
        # LEDGER's own tools: cost is already recorded by the controller; just note the call.
        runtime.collector.record(session_id=sid, source="agent_tool", operation="ledger_tool", query=tool, extra={"phase": "post"})
        return {}

    region_ids = _regions_from_tool(runtime, tool, tool_input, response, text)
    is_test = op == "execute" and pytest_args_from_command(str(tool_input.get("command") or "")) is not None

    runtime.collector.record(
        session_id=sid,
        source="agent_tool",
        operation=op,
        query=query,
        regions=region_ids,
        tokens_returned=estimate_tokens(text),
        bytes_returned=len(text.encode("utf-8", errors="replace")),
        success=not _response_failed(response),
        extra={"phase": "post", "tool": tool, "is_test": is_test},
    )
    if region_ids and op in {"read", "grep", "glob"}:
        runtime.controller.observe_agent_regions(sid, region_ids, intent_boost=0.25)

    if op == "edit" and path_arg:
        rel = _rel(runtime.cfg.repo_root, path_arg)
        stale = invalidate_path(runtime.conn, runtime.cfg, sid, rel, runtime.collector)
        new_ids = [r["region_id"] for r in regions_for_path(runtime.conn, rel) if r["kind"] != "module"]
        edited = _edited_regions(runtime, rel, tool_input) or new_ids[:3]
        runtime.collector.record(
            session_id=sid, source="edit", operation="edit", query=rel, regions=edited,
            extra={"tool": tool, "invalidated": stale},
        )
        runtime.controller.observe_edit(sid, edited)

    if is_test:
        cmd = str(tool_input.get("command") or "")
        # diagnostics the agent saw
        frames = parse_frames_from_text(text, runtime.cfg.repo_root)
        frame_ids = []
        for fr in frames:
            region = region_covering(runtime.conn, fr["path"], fr["line"])
            if region and region["kind"] != "module":
                frame_ids.append(region["region_id"])
        if frame_ids:
            runtime.controller.observe_execution(sid, list(dict.fromkeys(frame_ids)), high_priority=True)
        # program trace: re-run the same pytest invocation under coverage (both conditions, so cost is symmetric)
        if runtime.cfg.trace_agent_tests:
            args = pytest_args_from_command(cmd) or ["-q"]
            run_pytest_traced(
                runtime.cfg, sid, args=args, conn=runtime.conn, collector=runtime.collector,
                controller=None if runtime.cfg.observe_only else runtime.controller,
            )
    return {}


def handle_stop(runtime, payload: dict | None = None) -> dict:
    payload = payload if payload is not None else _read_stdin()
    sid = _session(runtime, payload)
    runtime.collector.record(session_id=sid, source="agent_tool", operation="complete", extra={"hook": "Stop"})
    runtime.end_session(sid, None)
    return {}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _op(tool: str) -> str:
    t = tool.lower()
    return {
        "grep": "grep", "glob": "glob", "read": "read", "edit": "edit", "multiedit": "edit", "write": "edit",
        "notebookedit": "edit", "bash": "execute",
    }.get(t, "ledger_tool" if t.startswith("mcp__ledger__") else (t or "execute"))


def _query_from_tool(tool: str, tool_input: dict) -> str | None:
    for key in ("pattern", "query", "objective", "file_path", "path", "command"):
        if tool_input.get(key):
            return str(tool_input[key])[:500]
    return tool or None


def _response_text(response: Any) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        # Claude Code Read: {"type":"text","file":{"filePath":..,"content":..}}
        f = response.get("file")
        if isinstance(f, dict) and "content" in f:
            return str(f["content"])
        for key in ("content", "stdout", "output", "text", "result"):
            v = response.get(key)
            if isinstance(v, str):
                extra = response.get("stderr") if key == "stdout" else ""
                return v + (("\n" + extra) if isinstance(extra, str) and extra else "")
            if isinstance(v, list):
                return "\n".join(_response_text(x) for x in v)
        return json.dumps(response)
    if isinstance(response, list):
        return "\n".join(_response_text(x) for x in response)
    return str(response)


def _response_failed(response: Any) -> bool:
    if isinstance(response, dict):
        if response.get("is_error") or response.get("isError"):
            return True
        if response.get("interrupted"):
            return True
    return False


def _rel(root: Path, path: str) -> str:
    p = Path(path)
    try:
        return str(p.resolve().relative_to(root.resolve())) if p.is_absolute() else str(p)
    except ValueError:
        return str(p)


def _edited_regions(runtime, rel: str, tool_input: dict) -> list[str]:
    """Locate the regions that an Edit touched by searching for new_string in the file."""
    new = tool_input.get("new_string")
    if not new and isinstance(tool_input.get("edits"), list):
        new = next((e.get("new_string") for e in tool_input["edits"] if e.get("new_string")), None)
    if not new:
        return []
    full = runtime.cfg.repo_root / rel
    if not full.exists():
        return []
    text = full.read_text(encoding="utf-8", errors="replace")
    idx = text.find(str(new))
    if idx < 0:
        return []
    start = text[:idx].count("\n") + 1
    end = start + str(new).count("\n")
    return [r["region_id"] for r in regions_in_range(runtime.conn, rel, start, end)]


def _regions_from_tool(runtime, tool: str, tool_input: dict, response: Any, text: str) -> list[str]:
    conn, cfg = runtime.conn, runtime.cfg
    op = _op(tool)
    ids: list[str] = []
    path_arg = tool_input.get("file_path") or tool_input.get("path")

    if op == "read" and path_arg:
        rel = _rel(cfg.repo_root, str(path_arg))
        offset = int(tool_input.get("offset") or 1)
        limit = tool_input.get("limit")
        if limit:
            ids = [r["region_id"] for r in regions_in_range(conn, rel, offset, offset + int(limit))]
        else:
            ids = [r["region_id"] for r in regions_for_path(conn, rel) if r["kind"] != "module"]
        if not ids:
            region = region_covering(conn, rel, offset)
            ids = [region["region_id"]] if region else []

    elif op == "grep":
        pattern = str(tool_input.get("pattern") or "")
        # exact: file:line hits in the response → covering regions
        for m in re.finditer(r"^(?P<path>[^\s:]+\.py)[:\-](?P<line>\d+)", text, re.M):
            region = region_covering(conn, _rel(cfg.repo_root, m.group("path")), int(m.group("line")))
            if region and region["kind"] != "module":
                ids.append(region["region_id"])
        if not ids and pattern:
            ids = [r["region_id"] for r in grep_regions(conn, pattern, 24)]
        filenames = response.get("filenames") if isinstance(response, dict) else None
        if isinstance(filenames, list) and not ids:
            for fn in filenames[:20]:
                ids.extend(r["region_id"] for r in regions_for_path(conn, _rel(cfg.repo_root, str(fn))) if r["kind"] != "module")

    elif op == "glob":
        filenames = response.get("filenames") if isinstance(response, dict) else None
        names = filenames if isinstance(filenames, list) else [l.strip() for l in text.splitlines() if l.strip().endswith(".py")]
        for fn in names[:20]:
            rel = _rel(cfg.repo_root, str(fn))
            rows = regions_for_path(conn, rel)
            ids.extend(r["region_id"] for r in rows if r["kind"] == "module")

    elif op == "edit" and path_arg:
        rel = _rel(cfg.repo_root, str(path_arg))
        ids = [r["region_id"] for r in regions_for_path(conn, rel) if r["kind"] != "module"]

    return list(dict.fromkeys(ids))
