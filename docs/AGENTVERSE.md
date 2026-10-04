# LEDGER as a Fetch.ai uAgent

`ledger/agentverse` wraps the same controller the MCP server uses, so another agent (or ASI:One chat) can ask for a budgeted working set without cloning the repository tools.

## Run locally (no account)

One command. It starts the local uAgent, sends the renewal-discount chat, prints the local address and a bundle that includes `for_renewal`, and writes `demo_repo/.ledger/agentverse_demo.json` with `fixture: false`.

```bash
source .venv/bin/activate
ledger agentverse-demo --repo demo_repo
```

If `AGENTVERSE_API_KEY` is unset, the command prints `falling back to local mode` and continues. It does not crash, it does not register a mailbox, and it does not submit the ASI:One form.

The two-process form is the same agent:

```bash
source .venv/bin/activate
pip install -e ".[agentverse]"
python -m ledger.agentverse --repo demo_repo --local --port 8000
```

The process prints a local demo identity derived from the committed public seed `ledger-runtime-local-demo-v1` (not an API key):

```
agent1qvntv3znytwfkn4u5zz9qsfekvw906l62k6hhg0e9xe3d6qx6s62cxaq2rq
```

That address is a **local demo identity, not an Agentverse-registered mailbox**. From another process:

```bash
python -m ledger.agentverse.client --local --port 8000 \
  "find the code for renewal invoices ignoring loyalty discounts"
python -m ledger.agentverse.client --local --port 8000 --chat \
  "find the code for renewal invoices ignoring loyalty discounts"
# or: ledger agentverse-ask --port 8000 --chat "find the code for renewal invoices ignoring loyalty discounts"
```

The reply includes `for_renewal` and `shop/billing/discount_policy.py`. The chat reply also includes a fenced JSON object. Inbound text is data: it is not passed to a shell or `eval`.

uAgents may publish protocol manifests and mark the localhost process active when the network is up. That is not mailbox registration.

## Mailbox registration (human steps, not done here)

`--mailbox` registers only when both environment variables are set. If either is missing, the process exits before it registers and does not invent an address.

1. Create an Agentverse API key in the Agentverse UI.
2. `export AGENTVERSE_API_KEY=...` (do not commit it).
3. `export AGENT_SEED=...` (a seed you choose for the mailbox identity; keep it private).
4. `python -m ledger.agentverse --repo demo_repo --mailbox`
5. The process POSTs that key to the local inspector `/connect` endpoint. If Agentverse rejects it, the log says registration failed.
6. The ASI:One **Submission Agent** form on the hackathon site is a separate step. This repository does not submit that form.

The placeholder `agent1q<LEDGER_RUNTIME_AGENTVERSE_ADDRESS>` is only a reminder that a mailbox address does not exist until step 4 succeeds. Do not paste the local demo address into that form and call it registered.

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

## ASI:One chat

ASI:One talks the official `AgentChatProtocol` (`uagents_core.contrib.protocols.chat`): inbound `ChatMessage`, outbound `ChatAcknowledgement` plus a `ChatMessage` reply. `ledger/agentverse/asi_one.py` is that handler. Text is keyword-routed by `parse_intent` — the same path as the structured `ChatText` fallback on `LedgerContextProtocol` if the official package is missing.

Verify the CLI surface:

```bash
python -m ledger.agentverse --help
```

## Chat

Plain `ChatText` (and ASI:One `ChatMessage` text) is keyword-routed by `parse_intent`:

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
