# Prize tracks

LEDGER Runtime is aimed at three MHacks 2026 tracks.

## Grand Prize

A complete, judge-runnable system: index a real (if small) repository, join
agent search with pytest execution coverage, serve a budgeted working set,
and show the decisions on a live dashboard. Evaluation is A/B against a
fixed simulated agent — same task, same repo, same tests.

## Actually Intelligent

The intelligence is not a new model. It is an online controller: admit /
prefetch / evict / invalidate over source regions, with deterministic
explanations and a freshness guard (content-hash before serve). The claim
is that context *control* is the missing systems layer, not another prompt.

## Fetch.ai ASI:One Agent Challenge

`ledger/agentverse` exposes the same controller as a uAgent:

- structured `LedgerContextProtocol` (context / search / explain / trace / metrics)
- keyword-routed `ChatText` for ASI:One-style conversations
- `python -m ledger.agentverse --repo demo_repo --local`
- example client: `python -m ledger.agentverse.client <address> "<objective>"`

Run locally without mailbox credentials. Mailbox mode is opt-in (`--mailbox`).
See `docs/AGENTVERSE.md`.
