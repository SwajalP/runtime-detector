# LEDGER Runtime

Coding agents repeatedly grep, glob, and reopen repositories because they do not have a persistent model of the current task’s code working set. LEDGER Runtime sits between an agent and its repository tools. It observes search/read/edit events, joins them with source regions executed by targeted tests, and maintains a versioned context cache under a strict token budget. Like a memory-hierarchy controller, LEDGER admits high-value regions, prefetches likely neighbors, evicts cold context, invalidates edited code, and falls back to raw search whenever confidence is low.

This is a **logical context cache** for source regions, not a model provider’s KV cache.

Same model. Same task. Same repository.

Measured A/B (`ledger eval --repo demo_repo --all`, simulated agent, 12 tasks × 1, 2026-10-04, 12.3 s). LEDGER’s injected tokens and context calls are counted against LEDGER.

| Condition | Success | Repo search/read calls | Total tool calls (incl. LEDGER) | Repo tokens (incl. injected) | Median time to target | Mean calls to target |
|---|---:|---:|---:|---:|---:|---:|
| Baseline | 12/12 | 191 | 191 | 84,396 | 0.383 s | 2.167 |
| LEDGER | 12/12 | 9 | 65 | 22,960 | 0.411 s | 1.0 |
| Change | equal | −95.3% | −66.0% | −72.8% | +7.3% | −53.9% |

Controller on this run: cache hit rate 0.889 · prefetch precision *n/a* (0 prefetches used) · pollution rate 0.0 · stale served 0 · fallback rate 0.0.

| Task | Family | Base ✓ | LEDGER ✓ | Base calls | LEDGER calls | Base tokens | LEDGER tokens | Δ tokens |
|---|---|:-:|:-:|---:|---:|---:|---:|---:|
| renewal-discount | bug | ✓ | ✓ | 20 | 7 | 11,163 | 2,553 | −77.1% |
| calculate-total | cross-layer | ✓ | ✓ | 18 | 6 | 7,465 | 2,093 | −72.0% |
| invoice-save | cross-layer | ✓ | ✓ | 13 | 4 | 6,706 | 1,600 | −76.1% |
| renewal-controller | cross-layer | ✓ | ✓ | 10 | 4 | 4,703 | 1,710 | −63.6% |
| failing-test | bug | ✓ | ✓ | 20 | 7 | 9,995 | 2,373 | −76.3% |
| promotion-confuser | bug | ✓ | ✓ | 19 | 6 | 9,301 | 2,353 | −74.7% |
| tax-adapter | cross-layer | ✓ | ✓ | 12 | 5 | 3,674 | 1,857 | −49.5% |
| schema-invoice | cross-layer | ✓ | ✓ | 16 | 4 | 7,672 | 1,369 | −82.2% |
| repeat-loyalty-lookup | repeated | ✓ | ✓ | 18 | 6 | 8,376 | 2,118 | −74.7% |
| webhook-renewal | cross-layer | ✓ | ✓ | 13 | 5 | 4,831 | 2,067 | −57.2% |
| held-out-proration | repeated | ✓ | ✓ | 15 | 5 | 4,602 | 1,297 | −71.8% |
| held-out-dunning | bug | ✓ | ✓ | 17 | 6 | 5,908 | 1,570 | −73.4% |

Re-run `ledger eval --repo demo_repo --all` and replace this table if the code or tasks change. Do not invent metrics.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
ledger init --repo demo_repo
ledger serve --repo demo_repo --port 8765
```

Dashboard: http://127.0.0.1:8765 — timeline, working set, code knowledge graph, L0–L2 memory hierarchy.

A/B evaluation (simulated agent, no model key required):

```bash
ledger eval --repo demo_repo --task renewal-discount
ledger eval --repo demo_repo --all
```

Claude Code (primary adapter):

```bash
ledger run --agent claude --repo demo_repo
# then, in another terminal, inside demo_repo:
claude
```

Fetch.ai uAgent (optional extra):

```bash
pip install -e ".[agentverse]"
python -m ledger.agentverse --help
ledger agentverse --repo demo_repo --local --port 8000
```

ASI:One uses the official `AgentChatProtocol` handler in `ledger/agentverse/asi_one.py`. Published address placeholder (replace after `ledger agentverse --mailbox`): `agent1q<LEDGER_RUNTIME_AGENTVERSE_ADDRESS>`. See `docs/AGENTVERSE.md`.

`ledger init` writes Claude Code **PreToolUse / PostToolUse / SessionStart** hooks and an MCP server (`.mcp.json`) exposing:

- `ledger_search(query, budget)`
- `ledger_context(seed, objective, budget)`
- `ledger_explain(bundle_id)`

The agent keeps Grep, Read, Bash, and Edit. LEDGER annotates repeated broad search, serves a budgeted bundle, and never hides raw search.

## What it measures

Task success is reported before efficiency. Primary cost metrics: repository tool calls, repository tokens returned, time to first target region, cache hit/miss, prefetch, eviction, and invalidation (stale-serve target is zero).

## Layout

```
ledger/           controller, index, traces, MCP, Claude hooks, Agentverse, eval
dashboard/        React + Vite UI (optional; FastAPI serves dashboard.html)
demo_repo/        layered billing shop with a deterministic renewal-discount bug
docs/             architecture, Agentverse, prize tracks
scripts/          reset + baseline/LEDGER runners
```
