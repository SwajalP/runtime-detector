"""Minimal MCP server over stdio (newline-delimited JSON-RPC 2.0).

Exposes three tools: traceweaver_search, traceweaver_context, traceweaver_explain. No SDK
dependency so the server starts in milliseconds and works inside hook-driven
Claude Code sessions. Model-supplied strings are treated as data only — they are
never executed or interpolated into shell commands.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from traceweaver.mcp.tools import TOOLS, render_bundle_text, render_search_text
from traceweaver.runtime import TraceWeaverRuntime

PROTOCOL_VERSION = "2025-06-18"


def _ok(id_, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _err(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _text(payload: dict, text: str, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
        "isError": is_error,
    }


def handle(runtime: TraceWeaverRuntime, message: dict, session_hint: str | None = None) -> dict | None:
    method = message.get("method")
    id_ = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        requested = params.get("protocolVersion") or PROTOCOL_VERSION
        return _ok(
            id_,
            {
                "protocolVersion": requested,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "traceweaver-runtime", "version": "0.1.0"},
                "instructions": (
                    "TraceWeaver serves budgeted, explainable repository context. Call traceweaver_context after the "
                    "first search or a failing test, and before repeating a repository-wide grep."
                ),
            },
        )
    if method in {"notifications/initialized", "initialized", "notifications/cancelled", "notifications/progress"}:
        return None
    if method == "ping":
        return _ok(id_, {})
    if method == "tools/list":
        return _ok(id_, {"tools": TOOLS})
    if method in {"resources/list", "prompts/list"}:
        return _ok(id_, {"resources": [], "prompts": []} if method == "resources/list" else {"prompts": []})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _ok(id_, _text({}, "arguments must be an object", True))
        session_id = runtime.ensure_session(agent="claude", condition="traceweaver", external_id=session_hint)
        try:
            if name == "traceweaver_search":
                query = str(args.get("query", ""))[:500]
                budget = _int(args.get("budget"))
                result = runtime.controller.search(session_id, query, budget)
                return _ok(id_, _text(result, render_search_text(result)))
            if name == "traceweaver_context":
                objective = str(args.get("objective") or runtime.objective(session_id) or "")[:2000]
                seed = args.get("seed")
                seed = str(seed)[:200] if seed else None
                budget = _int(args.get("budget"))
                result = runtime.controller.build_bundle(session_id, objective=objective, seed=seed, budget=budget)
                return _ok(id_, _text(result, render_bundle_text(result)))
            if name == "traceweaver_explain":
                bundle_id = str(args.get("bundle_id", ""))[:64]
                result = runtime.controller.explain(bundle_id)
                return _ok(id_, _text(result, result.get("text") or json.dumps(result, indent=2), "error" in result))
            return _err(id_, -32601, f"unknown tool {name}")
        except Exception as exc:  # report, never crash the transport
            return _ok(id_, _text({"error": str(exc)}, f"TraceWeaver error: {exc}", True))

    if id_ is not None:
        return _err(id_, -32601, f"unknown method {method}")
    return None


def _int(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _read_message() -> dict | None:
    """Read one message. Supports NDJSON (MCP stdio) and Content-Length framing."""
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith(b"content-length:"):
            length = int(stripped.split(b":", 1)[1].strip() or 0)
            # consume header block
            while True:
                hdr = sys.stdin.buffer.readline()
                if not hdr or hdr in {b"\r\n", b"\n"}:
                    break
            body = sys.stdin.buffer.read(length)
            try:
                return json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                continue
        try:
            return json.loads(stripped.decode("utf-8"))
        except json.JSONDecodeError:
            continue


def _write_message(msg: dict) -> None:
    sys.stdout.buffer.write((json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def serve_stdio(runtime: TraceWeaverRuntime | None = None) -> None:
    runtime = runtime or TraceWeaverRuntime()
    runtime.sync()
    session_hint = None
    while True:
        message = _read_message()
        if message is None:
            break
        if isinstance(message, list):  # batch
            replies = [r for r in (handle(runtime, m, session_hint) for m in message) if r]
            if replies:
                _write_message(replies)  # type: ignore[arg-type]
            continue
        reply = handle(runtime, message, session_hint)
        if reply is not None:
            _write_message(reply)
