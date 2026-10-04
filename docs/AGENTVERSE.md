# LEDGER as a Fetch.ai uAgent

`ledger/agentverse` wraps the same controller the MCP server uses, so another agent (or ASI:One chat) can ask for a budgeted working set without cloning the repository tools.

## Run locally (no mailbox credentials)

```bash
source .venv/bin/activate
pip install -e ".[agentverse]"
ledger init --repo demo_repo
ledger agentverse --repo demo_repo --local --port 8000
# or: python -m ledger.agentverse --repo demo_repo --local
```

The agent binds `http://127.0.0.1:8000/submit` and logs its address on startup.

Mailbox / Agentverse registration is opt-in:

```bash
ledger agentverse --repo demo_repo --mailbox
```

## LedgerContextProtocol

Structured request → reply pairs (inbound strings are data only — never executed):

| Request | Reply |
|---|---|
| `ContextRequest(objective, seed?, budget?)` | `ContextBundle` |
| `SearchRequest(query, budget?)` | `SearchResponse` |
| `ExplainRequest(bundle_id)` | `ExplainResponse` |
| `TraceRequest(pytest_args)` | `TraceResponse` |
| `MetricsRequest(session_id?)` | `MetricsResponse` |

Pytest arguments are whitelisted to repo-relative `*.py[::node]` paths and a small flag set (`-q`, `-x`, `--tb=short`, …).

## Chat

Plain `ChatText` is keyword-routed by `parse_intent`:

- `<objective>` or `context <objective>` — build a bundle
- `search for_renewal` — signatures only
- `trace tests/test_renewal_discount.py` — coverage-joined regions
- `explain bnd_…` — score breakdown
- `metrics` — hit/miss/prefetch/invalidation counters

Example client (separate process):

```bash
python -m ledger.agentverse.client <agent-address> "find the loyalty discount on annual renewal" --seed for_renewal
```

Each remote sender gets its own LEDGER session (working sets are not shared).
