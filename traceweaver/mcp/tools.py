"""MCP tool schemas and agent-facing text renderings."""

from __future__ import annotations

TOOLS = [
    {
        "name": "traceweaver_search",
        "title": "TraceWeaver budgeted search",
        "description": (
            "Rank source regions for a query using lexical, structural, runtime-execution and session "
            "history signals, returning signatures with exact file:line ranges under a token budget. "
            "Prefer this over repeating repository-wide grep. Raw Grep/Read remain available."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Symbol, error text, or natural-language intent"},
                "budget": {"type": "integer", "description": "Max tokens to return (default 1200)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "traceweaver_context",
        "title": "TraceWeaver context bundle",
        "description": (
            "Serve a bounded, explainable context bundle for the current task: the working set inferred from "
            "your search trajectory joined with the code executed by the most recent targeted test. Exact code "
            "is hash-verified against disk before serving. Call after the first search or a failing test, and "
            "before a second repository-wide search. Fall back to Grep/Read if the bundle is insufficient."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "objective": {"type": "string", "description": "What you are trying to do (task statement)"},
                "seed": {"type": "string", "description": "Best seed: a symbol, failing test name, or error string"},
                "budget": {"type": "integer", "description": "Max tokens for the bundle (default 2400)"},
            },
            "required": ["objective"],
        },
    },
    {
        "name": "traceweaver_explain",
        "title": "TraceWeaver explain bundle",
        "description": "Show the deterministic score breakdown, admission reason, evictions and rejections for a bundle.",
        "inputSchema": {
            "type": "object",
            "properties": {"bundle_id": {"type": "string"}},
            "required": ["bundle_id"],
        },
    },
]


def render_bundle_text(bundle: dict) -> str:
    lines = [
        f"TraceWeaver bundle {bundle['bundle_id']} · {bundle['token_count']}/{bundle['budget']} tokens · "
        f"{len(bundle['entries'])} regions from {bundle.get('candidates', 0)} candidates",
        f"Objective: {bundle.get('objective')}",
        "",
    ]
    for i, e in enumerate(bundle["entries"], 1):
        pin = " [pinned]" if e.get("pinned") else ""
        lines.append(f"{i}. {e['symbol']}  ({e['path']}:{e['start_line']}-{e['end_line']})  score={e['score']}  level={e['level']}{pin}")
        lines.append(f"   why: {e['why']}")
        lines.append(f"   hash: {e['content_hash']}")
        lines.append("   ```python")
        lines.extend("   " + l for l in (e.get("code") or "").splitlines())
        lines.append("   ```")
    if bundle.get("prefetch"):
        lines.append("")
        lines.append("Prefetched neighbours (signatures only):")
        for p in bundle["prefetch"]:
            lines.append(f"- {p['symbol']}  ({p['path']}:{p['start_line']}-{p['end_line']})  p={p['p_used_soon']}  {p['signature'].splitlines()[0]}")
    if bundle.get("rejected"):
        lines.append("")
        lines.append("Considered but not admitted: " + ", ".join(f"{r['symbol']} ({r['reason']})" for r in bundle["rejected"][:5]))
    if bundle.get("stale_blocked"):
        lines.append("")
        lines.append("Stale regions blocked (content changed on disk, re-indexed): " + ", ".join(s["symbol"] or s["path"] for s in bundle["stale_blocked"]))
    lines.append("")
    lines.append(f"Use traceweaver_explain('{bundle['bundle_id']}') for the score breakdown. Raw Grep/Read remain available.")
    return "\n".join(lines)


def render_search_text(result: dict) -> str:
    lines = [f"TraceWeaver search '{result['query']}' · {result['token_count']}/{result['budget']} tokens · {len(result['regions'])} regions"]
    for i, r in enumerate(result["regions"], 1):
        lines.append(f"{i}. {r['symbol']}  ({r['path']}:{r['start_line']}-{r['end_line']})  score={r['score']}  — {r['why']}")
        lines.append("   " + (r.get("signature") or "").splitlines()[0])
    return "\n".join(lines)
