# Honest limits

LEDGER is a working local system. These are the boundaries of what was actually run.

## What the demo is

The shop in `demo_repo` is a synthetic billing repository. The agent in `ledger eval` is a fixed simulated policy, not a live Claude Code A/B and not SWE-bench. Most tasks score localization (the target file shows up in the trajectory). `renewal-discount` is a patch task in `tasks.yaml`; the hook driver in `ledger ab` scores whether that target file was seen. The recorded eval is a single repeat. There is no confidence interval.

## Two different measurements

**Suite eval measured 2026-10-04** (`ledger eval --repo demo_repo --all`, simulated agent, 12 tasks × 1, 12.9 s), after the call-graph fix. Same numbers as the README:

- Success 12/12 both conditions.
- Repo search/read calls 203 → 8 (−96.1%). Repo tokens including injected context 94,462 → 24,744 (−73.8%).
- Median time-to-target 0.4 s → 0.421 s (+5.2%). Mean calls-to-target 2.167 → 1.0.
- Prefetch precision 0.8 (5 prefetches, 4 later used). `renewal-discount` was 1/1. Held-out tasks issued 0. Cap is 2 per bundle.
- Stale served 0. Pollution rate 0.0. Fallback rate 0.0. Cache hit rate 0.907.

Time-to-target is worse because LEDGER runs coverage-traced pytest before it can join the program trace. Baseline pytest is untraced and cheaper. Calls-to-target is the fairer localization metric.

**Hook A/B measured this session** (`ledger ab --task renewal-discount --repo demo_repo`, 2026-10-04). Same Claude hook and MCP schema. `claude -p` returned `Invalid API key · Please run /login` (`claude.ran: false`):

| Condition | Target found | Advice | Repo calls | Ledger calls | Repo tokens |
|---|---|---:|---:|---:|---:|
| Baseline (observe-only) | yes | 0 | 20 | 0 | 9,317 |
| LEDGER (advice + ledger_context/ledger_search) | yes | 1 | 4 | 3 | 4,928 |

That ledger session issued 1 prefetch and used 0 (precision 0.0 on this one task). That is not the suite-wide precision of 0.8. Prefetch stays capped at 2 callees of high-score admitted regions. Spraying dozens of unused prefetches is a failed optimization; this run did not do that.

## Live Claude

`claude` is on PATH. `claude -p` returned `Invalid API key · Please run /login`. `ledger ab` stores that under `claude.ran: false`. It does not copy hook-driver numbers into a fake Claude row. A human still has to run `/login` before `ledger eval --agent claude` or a logged-in `claude -p` can be a live model A/B.

`ledger run --agent claude` installs real PreToolUse / PostToolUse hooks and `.mcp.json` using the venv interpreter. `LEDGER_OBSERVE_ONLY=1` records events and does not advise. That install path was not A/B-measured with a live Claude model.

## Stale serve

Stale-serve is 0 on the recorded suite because exact code is hash-checked before it is served. A mismatch is invalidated and is not counted as a hit. That is a design invariant of this controller, not a large-N proof.

## Tracing and retrieval

Runtime tracing is Python and pytest/coverage only. Tree-sitter (or the ast fallback) is syntax. It is not a perfect call graph.

This is not the model KV cache. It is not a claim that grep is slow. Retrieval of the kind Aider, CodeGrep, and CodeNib already do is not claimed as new. The contribution is the online dual-trace controller: admit, prefetch, evict, and invalidate source regions under a token budget, joining agent events with test execution.

Policy weights in `ledger/config.py` are constants, not a trained model. There is one demo repository. Lexical traps under `shop/promotions` and `shop/legacy` are intentional.

## Agentverse

Local demo identity, derived from the committed public seed `ledger-runtime-local-demo-v1` (not an API key):

```
agent1qvntv3znytwfkn4u5zz9qsfekvw906l62k6hhg0e9xe3d6qx6s62cxaq2rq
```

That address is a local demo identity, not an Agentverse-registered mailbox. This session started `python -m ledger.agentverse --repo demo_repo --local --port 8099` and, from another process, `python -m ledger.agentverse.client --local --port 8099` with the utterance `find the code for renewal invoices ignoring loyalty discounts`. The reply included `for_renewal` in `shop/billing/discount_policy.py`, plus structured JSON on the chat path.

uAgents may publish protocol manifests and mark the localhost process active when the network is reachable. That is not mailbox registration and not the ASI:One Submission Agent form.

Mailbox mode (`--mailbox`) exits with an error when `AGENTVERSE_API_KEY` or `AGENT_SEED` is missing. This environment had neither. The ASI:One submission form was not submitted.

## Dashboard

`dashboard/dist` is optional and gitignored. `ledger serve` uses a built Vite `index.html` when present, otherwise the built-in `dashboard.html`.

## Replay

`ledger replay` and `./scripts/reset_demo.sh --replay` reload a recorded fixture. The session agent is `replay` and the label starts with `REPLAY`. That is not a live model.
