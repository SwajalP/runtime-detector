# Three-minute demo

Offline first. No model key. Do not present replay as live Claude.

```bash
source .venv/bin/activate
./scripts/reset_demo.sh
ledger serve --repo demo_repo --port 8765
```

Open http://127.0.0.1:8765

## 0:00–0:40 — the claim

LEDGER is a **logical context cache** for source regions. It is not a
provider KV cache. It is not “grep is slow.” Same model, same task, same
repo: the agent wastes tokens by searching without a working set.

## 0:40–1:40 — measured A/B (simulated)

In a second terminal:

```bash
source .venv/bin/activate
ledger eval --repo demo_repo --task renewal-discount
```

Point at the table: success held, fewer repo search/read calls, fewer
tokens (LEDGER-injected context is counted against LEDGER). Prefetch on
this task should be 1 unused call successor with precision 1.0.

If there is time: `ledger eval --repo demo_repo --all` (12/12, repo calls
191 → 9, tokens 84,396 → 23,647 on the 2026-10-04 simulated run). Say
out loud that median time-to-target is worse because LEDGER traces pytest
first; calls-to-target is better (1.0 vs 2.167).

## 1:40–2:20 — dashboard

On the live page: timeline (admit / prefetch / bundle), working set,
code knowledge graph, L0–L2 hierarchy. Open a bundle explanation if the
simulated run is still the active session.

## 2:20–2:45 — venue Wi-Fi failure

Do **not** start Claude. Replay the committed fixture:

```bash
./scripts/reset_demo.sh --replay
# dashboard already serving: refresh
```

The session agent is `replay` and the label starts with `REPLAY`.
Banner: recorded events, not a live model.

## 2:45–3:00 — Agentverse (local only)

```bash
ledger agentverse --repo demo_repo --local --port 8000
```

Same controller over `LedgerContextProtocol` / ASI:One chat handler.
Mailbox address is a placeholder; do not claim a published Agentverse
registration unless you have one.

Stop. Do not invent metrics. Do not claim a Claude live A/B you did not run.
