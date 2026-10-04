# LEDGER Runtime

Coding agents repeatedly grep, glob, and reopen repositories because they do not have a persistent model of the current task’s code working set. LEDGER Runtime sits between an agent and its repository tools. It observes search/read/edit events, joins them with source regions executed by targeted tests, and maintains a versioned context cache under a strict token budget. Like a memory-hierarchy controller, LEDGER admits high-value regions, prefetches likely neighbors, evicts cold context, invalidates edited code, and falls back to raw search whenever confidence is low.

This is a **logical context cache** for source regions, not a model provider’s KV cache.

Same model. Same task. Same repository.

Measured A/B (`ledger eval --repo demo_repo --all`, simulated agent, 12 tasks × 1, 2026-10-04, 12.9 s). LEDGER’s injected tokens and context calls are counted against LEDGER.

**Headline: equal success, far fewer repository calls and tokens.** 12/12 both conditions. Repo search/read calls −96.1% (203 → 8). Repo tokens including LEDGER-injected context −73.8% (94,462 → 24,744).

| Condition | Success | Repo search/read calls | Total tool calls (incl. LEDGER) | Repo tokens (incl. injected) | Median time to target | Mean calls to target |
|---|---:|---:|---:|---:|---:|---:|
| Baseline | 12/12 | 203 | 203 | 94,462 | 0.4 s | 2.167 |
| LEDGER | 12/12 | 8 | 64 | 24,744 | 0.421 s | 1.0 |
| Change | equal | −96.1% | −68.5% | −73.8% | +5.2% | −53.9% |

Controller on this run: cache hit rate 0.907 · prefetch precision 0.8 (5 prefetches, 4 later used — capped at 2 per bundle; renewal-discount was 1/1; held-out tasks issued 0) · pollution rate 0.0 · stale served 0 · fallback rate 0.0.

Median time-to-target is worse (+5.2%). That clock includes the coverage-traced pytest LEDGER runs first so the program trace can join the agent trace; baseline’s pytest is untraced and cheaper. Calls-to-target is better (1.0 vs 2.167): the target region is already in the first bundle. Do not read the time delta as a localization regression.

| Task | Family | Base ✓ | LEDGER ✓ | Base calls | LEDGER calls | Base tokens | LEDGER tokens | Δ tokens |
|---|---|:-:|:-:|---:|---:|---:|---:|---:|
| renewal-discount | bug | ✓ | ✓ | 21 | 9 | 11,851 | 2,916 | −75.4% |
| calculate-total | cross-layer | ✓ | ✓ | 18 | 5 | 7,961 | 2,027 | −74.5% |
| invoice-save | cross-layer | ✓ | ✓ | 13 | 4 | 6,780 | 1,566 | −76.9% |
| renewal-controller | cross-layer | ✓ | ✓ | 11 | 4 | 5,515 | 1,720 | −68.8% |
| failing-test | bug | ✓ | ✓ | 22 | 6 | 10,970 | 2,386 | −78.2% |
| promotion-confuser | bug | ✓ | ✓ | 19 | 6 | 9,777 | 2,315 | −76.3% |
| tax-adapter | cross-layer | ✓ | ✓ | 13 | 5 | 3,827 | 1,829 | −52.2% |
| schema-invoice | cross-layer | ✓ | ✓ | 16 | 5 | 7,595 | 1,563 | −79.4% |
| repeat-loyalty-lookup | repeated | ✓ | ✓ | 21 | 5 | 9,411 | 2,049 | −78.2% |
| webhook-renewal | cross-layer | ✓ | ✓ | 15 | 5 | 7,021 | 2,282 | −67.5% |
| held-out-proration | repeated | ✓ | ✓ | 15 | 5 | 4,939 | 1,356 | −72.5% |
| held-out-dunning | bug | ✓ | ✓ | 19 | 5 | 8,815 | 2,735 | −69.0% |

Re-run `ledger eval --repo demo_repo --all` and replace this table if the code or tasks change. Do not invent metrics. The 12-task table above is the suite eval measured 2026-10-04 after the call-graph fix (12.9 s).

## Hook A/B (no Claude login)

`ledger ab --task renewal-discount --repo demo_repo` drives the Claude hook and MCP schema itself. Baseline is observe-only (raw grep/read, no advice). LEDGER advises and calls `ledger_context` / `ledger_search`. Measured 2026-10-04:

| Condition | Target found | Advice | Repo calls | Ledger calls | Repo tokens |
|---|---|---:|---:|---:|---:|
| Baseline (observe-only) | yes | 0 | 20 | 0 | 9,317 |
| LEDGER | yes | 1 | 4 | 3 | 4,928 |

`claude -p` was probed and did not run: `Invalid API key · Please run /login`. That fact is stored on the report as `claude.ran: false`. Hook numbers are not labeled as a live Claude result.

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

Hook-schema A/B (no Claude login; same hooks the Claude adapter uses):

```bash
ledger ab --task renewal-discount --repo demo_repo
# or: ./scripts/run_claude_ab.sh
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

ASI:One uses the official `AgentChatProtocol` handler in `ledger/agentverse/asi_one.py`. Local demo identity (seed `ledger-runtime-local-demo-v1`, not an Agentverse-registered mailbox): `agent1qvntv3znytwfkn4u5zz9qsfekvw906l62k6hhg0e9xe3d6qx6s62cxaq2rq`.

```bash
python -m ledger.agentverse --repo demo_repo --local --port 8000
python -m ledger.agentverse.client --local --port 8000 \
  "find the code for renewal invoices ignoring loyalty discounts"
```

Mailbox registration needs `AGENTVERSE_API_KEY` and `AGENT_SEED` and errors when they are absent. The ASI:One submission form was not submitted. See `docs/AGENTVERSE.md`.

`ledger init` writes Claude Code **PreToolUse / PostToolUse / SessionStart** hooks and an MCP server (`.mcp.json`) exposing:

- `ledger_search(query, budget)`
- `ledger_context(seed, objective, budget)`
- `ledger_explain(bundle_id)`

The agent keeps Grep, Read, Bash, and Edit. LEDGER annotates repeated broad search, serves a budgeted bundle, and never hides raw search.

## What it measures

Task success is reported before efficiency. Primary cost metrics: repository tool calls, repository tokens returned, time to first target region, cache hit/miss, prefetch, eviction, and invalidation (stale-serve target is zero).

Judge copy: 50- and 150-word blurbs in `docs/TRACKS.md`. Three-minute script: `docs/DEMO.md`. Venue Wi-Fi failure: `./scripts/reset_demo.sh --replay` (labelled REPLAY, not live Claude).

## Honest limits

Simulated 12-task eval, not a live Claude A/B and not SWE-bench. Most tasks score whether the target file was seen. One repeat, no confidence interval. Median time-to-target on that eval is worse (+5.2%, 0.4 s → 0.421 s) because LEDGER runs coverage-traced pytest first; calls-to-target is the fairer localization metric (1.0 vs 2.167). Suite prefetch precision on that eval is 0.8 (5 issued, 4 used). The hook A/B above is a separate measurement (one prefetch issued, none used). Stale-serve 0 is the hash-before-serve invariant, not a large-N proof. Tracing is Python + pytest/coverage. Tree-sitter is syntax. This is not a model KV cache and not “grep is slow.” Retrieval is not claimed as new (Aider, CodeGrep, CodeNib); the contribution is the online dual-trace controller. Policy weights are constants. One demo repo; lexical traps are intentional. Vite `dashboard/dist` is optional and gitignored. Full write-up: `docs/LIMITATIONS.md`.

## Layout

```
ledger/           controller, index, traces, MCP, Claude hooks, Agentverse, eval
dashboard/        React + Vite UI (optional; serve prefers dist/ if built, else dashboard.html)
demo_repo/        layered billing shop with a deterministic renewal-discount bug
docs/             architecture, Agentverse, prize tracks, 3-minute demo
scripts/          reset + baseline/LEDGER runners + offline replay
```
