# LEDGER Runtime

Coding agents repeatedly grep, glob, and reopen repositories because they do not have a persistent model of the current task’s code working set. LEDGER Runtime sits between an agent and its repository tools. It observes search/read/edit events, joins them with source regions executed by targeted tests, and maintains a versioned context cache under a strict token budget. Like a memory-hierarchy controller, LEDGER admits high-value regions, prefetches likely neighbors, evicts cold context, invalidates edited code, and falls back to raw search whenever confidence is low.

This is a **logical context cache** for source regions, not a model provider’s KV cache.

Same model. Same task. Same repository.

```
Baseline vs LEDGER: run `ledger eval --repo demo_repo` and paste measured numbers here.
```

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
ledger init --repo demo_repo
ledger serve --repo demo_repo --port 8765
```

Dashboard: http://127.0.0.1:8765

A/B evaluation (simulated agent, no model key required):

```bash
ledger eval --repo demo_repo --task renewal-discount
ledger eval --repo demo_repo
```

Claude Code (primary adapter):

```bash
ledger run --agent claude --repo demo_repo
# then, in another terminal, inside demo_repo:
claude
```

`ledger init` writes Claude Code **PreToolUse / PostToolUse / SessionStart** hooks and an MCP server (`.mcp.json`) exposing:

- `ledger_search(query, budget)`
- `ledger_context(seed, objective, budget)`
- `ledger_explain(bundle_id)`

The agent keeps Grep, Read, Bash, and Edit. LEDGER annotates repeated broad search, serves a budgeted bundle, and never hides raw search.

## What it measures

Task success is reported before efficiency. Primary cost metrics: repository tool calls, repository tokens returned, time to first target region, cache hit/miss, prefetch, eviction, and invalidation (stale-serve target is zero).

## Layout

```
ledger/           controller, index, traces, MCP, Claude hooks, eval
dashboard/        React + Vite UI (optional; FastAPI also serves a built-in dashboard)
demo_repo/        layered billing shop with a deterministic renewal-discount bug
scripts/          reset + baseline/LEDGER runners
```
