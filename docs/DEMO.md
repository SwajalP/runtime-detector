# Three-minute demo

Commands below were run from `/Users/Lay/runtime-detector` on 2026-10-04 with `source .venv/bin/activate`. `OPENAI_API_KEY` and `AGENTVERSE_API_KEY` were unset. Nothing here is a live Claude or GPT measurement.

```bash
source .venv/bin/activate
ledger index --repo demo_repo
ledger integrate claude --repo demo_repo
ledger integrate cline --repo demo_repo
ledger integrate gpt --repo demo_repo
LEDGER_DUMMY=1 ledger gpt-turn --objective "renewal invoices ignore loyalty discounts" --repo demo_repo
ledger gpt-turn --objective "renewal invoices ignore loyalty discounts" --seed for_renewal --repo demo_repo
ledger audit --repo demo_repo
ledger ab --task renewal-discount --repo demo_repo
ledger serve --repo demo_repo --port 8765
```

In another terminal, local uAgent (no mailbox key):

```bash
source .venv/bin/activate
python -m ledger.agentverse --repo demo_repo --local --port 8021
python -m ledger.agentverse.client --local --port 8021 \
  "find the code for renewal invoices ignoring loyalty discounts"
```

`python -m ledger.agentverse --mailbox` with no `AGENTVERSE_API_KEY` printed `falling back to local mode` and kept serving on localhost. It did not crash and did not register a mailbox.

## What that run actually measured

`ledger index` (Tree-sitter bodies already in the index, 73 non-module regions). Card totals use the stored `token_count` column:

| | bytes | estimated tokens |
|---|---:|---:|
| full body | 20,967 | 5,212 |
| signature card | 6,327 | 1,554 |

`LEDGER_DUMMY=1` wrote `demo_repo/.ledger/last_gpt_turn.json` with `fixture: true`, `provider: "fixture"`, `live: false`. That file is `ledger/fixtures/gpt_trace.json`. It is not an OpenAI call.

The next `ledger gpt-turn` (no dummy, no API key) was `source: local_backup`, `fixture: false`, `live: false`. The bundle included `shop/billing/discount_policy.py::for_renewal`. Its explanation cites reverse BFS depth 3 from `test_renewal_applies_loyalty_on_annual_boundary`, Tarjan SCC size 5, and the Stoer–Wagner seed-side partition (21 regions).

`ledger audit` wrote `demo_repo/.ledger/last_audit.json`. Structural, not a SonarQube score. The indexed graph has 73 regions, 80 directed edges (80 calls resolved, 45 left unbound because they are builtins such as `int` and `list.append`).

Reverse BFS from `for_renewal` reaches the failing test at depth 3:

`for_renewal` ← `SubscriptionService.renew` ← `RenewalController.renew` ← `test_renewal_applies_loyalty_on_annual_boundary`

`calculate_total` is also a caller of `for_renewal` (depth 1). `loyalty_discount`, `InvoiceRepository.save`, and `TaxAdapter.for_amount` are on the renewal chain.

Tarjan: one SCC of size 5, the failed-charge retry (webhook → controller → service → `schedule_retry` → `deliver_retry` → webhook):

- `SubscriptionService.renew`
- `schedule_retry`
- `RenewalWebhook.handle`
- `deliver_retry`
- `RenewalController.renew`

Stoer–Wagner min-cut of that neighborhood (22 nodes) has weight 1.0. That is the true minimum: the neighborhood still has a bridge, so the cheapest cut is a single edge, not a denser boundary. The partition is the result. Other side: `shop/billing/dunning.py::next_retry_hours`. Seed side (21 regions that stay with `for_renewal`):

- `shop/billing/discount_policy.py::for_renewal`
- `shop/billing/discount_policy.py::loyalty_discount`
- `shop/billing/subscription_service.py::SubscriptionService.calculate_total`
- `shop/billing/subscription_service.py::SubscriptionService.renew`
- `shop/api/renewal_controller.py::RenewalController.renew`
- `shop/billing/invoice_repository.py::InvoiceRepository.save`
- `shop/billing/tax_adapter.py::TaxAdapter.for_amount`
- plus the other callers in that neighborhood (tests, `deliver_retry`, `RenewalWebhook.handle`, `schedule_retry`, `Money`, `Loyalty`, `Subscription`, `Invoice`)

Clone clusters: 0. Lexical traps: 32. `fixture_comparison` and `mailbox_status` are labeled `fixture: true`.

`ledger ab --task renewal-discount` wrote `demo_repo/.ledger/last_ab.json`. Hook schema, not live Claude (`claude -p` returned `Invalid API key · Please run /login`):

| condition | target found | advice | repo calls | ledger calls | repo tokens |
|---|---|---:|---:|---:|---:|
| baseline | yes | 0 | 20 | 0 | 9,317 |
| ledger | yes | 1 | 4 | 3 | 4,928 |

The dashboard at http://127.0.0.1:8765 loaded. `/api/health` was ok, `/api/graph` had 73 nodes, `/api/graph/hierarchy` listed L0–L2 and backing, and `/api/audit` was `fixture: false`. The structural-audit panel showed the SCC members, the seed-side partition, and the reverse-BFS depths, not only the weight.

The local client reply named `for_renewal` in `shop/billing/discount_policy.py` and cited reverse BFS depth 3, Tarjan SCC size 5, and the seed-side partition. Identity (seed `ledger-runtime-local-demo-v1`, not a registered mailbox): `agent1qvntv3znytwfkn4u5zz9qsfekvw906l62k6hhg0e9xe3d6qx6s62cxaq2rq`.

`python -m pytest tests -q`: 50 passed. The shop’s `test_renewal_applies_loyalty_on_annual_boundary` still fails; that is the bug under test.
