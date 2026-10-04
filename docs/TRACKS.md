# Prize tracks

LEDGER Runtime is aimed at three MHacks 2026 tracks.

## 50-word blurb

LEDGER Runtime is a logical context cache for coding agents, not a model
KV cache and not a claim that grep is slow. It joins agent search with
test execution and admits, prefetches, evicts, and invalidates source
regions under a token budget. Same model, same task, same repo. Simulated
A/B.

## 150-word blurb

Coding agents reopen the same files because they have no persistent
working set. LEDGER is an online controller, not a new model: it observes
search, read, and edit events, joins them with regions a targeted test
executed, and serves a hash-verified bundle under a token budget.
High-value regions are admitted; unused call successors may be
signature-prefetched; cold context is evicted; edited code is invalidated.
Raw Grep and Read stay available.

This is a logical cache of source regions, not a provider KV cache, and
not a claim that grep is the bottleneck. Same model.

Measured ledger eval --repo demo_repo --all (simulated agent, 12 tasks,
2026-10-04): 12/12 both conditions; repo calls 191 to 9; repo tokens
including injected context 84396 to 23647. Prefetch precision 0.5
suite-wide (6 issued, 3 used; renewal-discount 1/1). Time-to-target is
worse because LEDGER traces pytest first; calls-to-target is 1.0 vs
2.167. No Claude live A/B was run.

## Grand Prize

A complete, judge-runnable system: index a real (if small) repository, join
agent search with pytest execution coverage, serve a budgeted working set,
and show the decisions on a live dashboard. Evaluation is A/B against a
fixed simulated agent — same task, same repo, same tests. Three-minute
script: `docs/DEMO.md`. Offline fallback: `scripts/reset_demo.sh --replay`
(labelled REPLAY, not live Claude).

## Actually Intelligent

The intelligence is not a new model. It is an online controller: admit /
prefetch / evict / invalidate over source regions, with deterministic
explanations and a freshness guard (content-hash before serve). The claim
is that context *control* is the missing systems layer, not another prompt.

## Fetch.ai ASI:One Agent Challenge

`ledger/agentverse` exposes the same controller as a uAgent:

- structured `LedgerContextProtocol` (context / search / explain / trace / metrics)
- official ASI:One `AgentChatProtocol` (`ledger/agentverse/asi_one.py`) when `uagents_core.contrib.protocols.chat` is installed
- keyword-routed `ChatText` fallback for the same intents
- `python -m ledger.agentverse --help` / `--repo demo_repo --local`
- published address placeholder: `agent1q<LEDGER_RUNTIME_AGENTVERSE_ADDRESS>`
- example client: `python -m ledger.agentverse.client <address> "<objective>"`

Run locally without mailbox credentials. Mailbox mode is opt-in (`--mailbox`)
and was not registered in this slice. See `docs/AGENTVERSE.md`.
