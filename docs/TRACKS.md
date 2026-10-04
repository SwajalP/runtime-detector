# Prize tracks

TraceWeaver Runtime is aimed at three MHacks 2026 tracks.

## 50-word blurb

TraceWeaver Runtime is a logical context cache for coding agents, not a model
KV cache and not a claim that grep is slow. It joins agent search with
test execution and admits, prefetches, evicts, and invalidates source
regions under a token budget. Same model, same task, same repo. Simulated
A/B.

## 150-word blurb

Coding agents reopen the same files because they have no persistent
working set. TraceWeaver is an online controller, not a new model: it observes
search, read, and edit events, joins them with regions a targeted test
executed, and serves a hash-verified bundle under a token budget.
High-value regions are admitted; unused call successors may be
signature-prefetched; cold context is evicted; edited code is invalidated.
Raw Grep and Read stay available.

This is a logical cache of source regions, not a provider KV cache, and
not a claim that grep is the bottleneck. Same model.

Measured traceweaver eval --repo demo_repo --all (simulated agent, 12 tasks,
2026-10-04): 12/12 both conditions; repo calls 191 to 9; repo tokens
including injected context 84396 to 23647. Prefetch precision 0.5
suite-wide (6 issued, 3 used; renewal-discount 1/1). Time-to-target is
worse because TraceWeaver traces pytest first; calls-to-target is 1.0 vs
2.167. No Claude live A/B was run.

## Grand Prize

A complete, judge-runnable system: index a real (if small) repository, join
agent search with pytest execution coverage, serve a budgeted working set,
and show the decisions on a live dashboard. The same event log is a local
medallion lake (`traceweaver lake build`: bronze events, silver region
observations, gold working set / bundle / audit / A/B metrics). That lake
is files on disk. It is not a Databricks benchmark and it does not call
Databricks. Evaluation is A/B against a fixed simulated agent — same task,
same repo, same tests. Three-minute script: `docs/DEMO.md`. Offline
fallback: `scripts/reset_demo.sh --replay` (labelled REPLAY, not live Claude).

## Actually Intelligent

The intelligence is not a new model. It is an online controller: admit /
prefetch / evict / invalidate over source regions, with deterministic
explanations and a freshness guard (content-hash before serve). Gold metrics
keep the structural audit next to the A/B rows: Tarjan SCCs, reverse BFS
from `for_renewal` to the failing test, and the Stoer–Wagner partition.
The claim is that context *control* is the missing systems layer, not
another prompt.

## Fetch.ai ASI:One Agent Challenge

The agent takes a coding-task intent and returns a budgeted context bundle.
That bundle is the action: which source regions to read, under a token
budget, with a reason for each. It speaks ASI:One Chat Protocol
(`AgentChatProtocol` when installed, `ChatText` otherwise) plus the
structured `TraceWeaverContextProtocol`, and another agent can call it.

```bash
source .venv/bin/activate
traceweaver agentverse-demo --repo demo_repo
```

Missing `AGENTVERSE_API_KEY` falls back to the local uAgent and says so.
The local demo address (seed `traceweaver-runtime-local-demo-v1`, not a
registered mailbox) is
`agent1qfdq0jc7atjkx2vgc7fdexfc29ukr4ncacw33t0csuxydt23wuy0q7npan4`.
The command writes `demo_repo/.traceweaver/agentverse_demo.json` with
`fixture: false`. The ASI:One prize form was not submitted.

Human steps still required:

1. Create an Agentverse API key in the Agentverse UI. Do not commit it.
2. Choose a private `AGENT_SEED`. Do not commit it.
3. `export AGENTVERSE_API_KEY=...` and `export AGENT_SEED=...`
4. `python -m traceweaver.agentverse --repo demo_repo --mailbox`
5. Confirm the log shows a successful mailbox connect. That address comes from `AGENT_SEED`. Do not submit the local demo address as a registered mailbox.
6. Submit the hackathon ASI:One Submission Agent form yourself.

See `docs/AGENTVERSE.md` and `traceweaver/agentverse/README.md`.
