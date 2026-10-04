# Devpost copy (draft)

**LEDGER Runtime** is a semantic memory hierarchy for coding agents: an
online context-control layer that sits between the agent and repository
tools.

Coding agents grep, glob, and reopen the same files because they have no
persistent model of the current task’s working set. LEDGER observes
search/read/edit events, joins them with the source regions a targeted
test actually executed, and maintains a versioned context cache under a
strict token budget. Like a memory-hierarchy controller it admits
high-value regions, prefetches likely neighbours, evicts cold context,
invalidates edited code, and falls back to raw search when confidence is
low.

This is a **logical context cache** for source regions, not a model
provider’s KV cache.

## What to run

```bash
source .venv/bin/activate
pip install -e .
ledger init --repo demo_repo
ledger eval --repo demo_repo
ledger serve --repo demo_repo --port 8765
```

Dashboard: http://127.0.0.1:8765 — live timeline, code knowledge graph, and L0–L2 memory hierarchy.

Fetch.ai local agent (no mailbox credentials):

```bash
pip install -e ".[agentverse]"
ledger agentverse --repo demo_repo --local --port 8000
```

Paste **only** numbers produced by `ledger eval` into the public writeup.
Do not invent metrics.

## Tracks

See `docs/TRACKS.md` (Grand Prize, Actually Intelligent, Fetch.ai ASI:One).
