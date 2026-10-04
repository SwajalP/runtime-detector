# Three-minute demo

Run from `/Users/Lay/runtime-detector` on 2026-10-04 with `source .venv/bin/activate`. `OPENAI_API_KEY`, `AGENTVERSE_API_KEY`, `DATABRICKS_HOST`, and `DATABRICKS_TOKEN` were unset. Nothing below is a live Claude or GPT measurement, a registered Agentverse mailbox, or a Databricks job.

## Copy-paste

```bash
source .venv/bin/activate
ledger index --repo demo_repo
ledger ab --task renewal-discount --repo demo_repo
ledger audit --repo demo_repo
ledger lake build --repo demo_repo
ledger lake show --repo demo_repo
ledger agentverse-demo --repo demo_repo
ledger serve --repo demo_repo --port 8765
```

Say this while it runs:

1. **Index and A/B (about a minute).** The index is source regions, not a model cache. The A/B is the Claude hook schema with no login: observe-only baseline versus advice plus `ledger_context` / `ledger_search`. `claude -p` is recorded as not run when login fails.
2. **Audit and lake (about a minute).** Reverse BFS, one SCC, and the min-cut partition are structural facts about the call graph. The lake is local JSONL (bronze / silver / gold). The savings line names the file it was read from.
3. **Agent and dashboard (about a minute).** `ledger agentverse-demo` is a local uAgent round-trip. The address is a demo identity, not a mailbox. The dashboard strip shows bronze, silver, and gold counts next to the graph, the hierarchy, and the audit columns.

## What this session measured

`ledger index --repo demo_repo` (Tree-sitter, 73 non-module regions). This session also ran `ledger index --full` once so the stored index matched the tree before the audit below.

| | bytes | estimated tokens |
|---|---:|---:|
| full body | 20,967 | 5,212 |
| signature card | 6,327 | 1,554 |

`ledger ab --task renewal-discount --repo demo_repo` wrote `demo_repo/.ledger/last_ab.json`. Hook schema, not live Claude (`claude -p` returned `Invalid API key · Please run /login`):

| condition | target found | advice | repo calls | ledger calls | repo tokens |
|---|---|---:|---:|---:|---:|
| baseline | yes | 0 | 20 | 0 | 9,317 |
| ledger | yes | 1 | 4 | 3 | 4,931 |

`ledger lake show` reads that file and prints `savings source: last_ab.json`: repo calls 20 → 4 (−80.0%), repo tokens 9,317 → 4,931 (−47.1%).

The 12-task suite was not re-run. `demo_repo/.ledger/last_eval.json` is still on disk from the earlier simulated eval. Gold ingests it as its own row with `source_file: last_eval.json` (repo calls 203 → 8, repo tokens 94,462 → 24,744 in that file). `lake show` does not use it while `last_ab.json` is newer.

`ledger audit` wrote `demo_repo/.ledger/last_audit.json`. Structural, not a SonarQube score. 73 regions, 80 directed edges (80 calls resolved, 45 unbound builtins).

Reverse BFS from `for_renewal` reaches the failing test at depth 3:

`for_renewal` ← `SubscriptionService.renew` ← `RenewalController.renew` ← `test_renewal_applies_loyalty_on_annual_boundary`

`calculate_total` is also a caller of `for_renewal` (depth 1).

Tarjan: one SCC of size 5 (webhook → controller → service → `schedule_retry` → `deliver_retry` → webhook):

- `SubscriptionService.renew`
- `RenewalWebhook.handle`
- `deliver_retry`
- `RenewalController.renew`
- `schedule_retry`

Stoer–Wagner min-cut of that neighborhood (22 nodes) has weight 1.0. The other side is the bridge `shop/billing/dunning.py::next_retry_hours`. Seed side (21 regions that stay with `for_renewal`):

- `shop/billing/discount_policy.py::for_renewal`
- `shop/billing/discount_policy.py::loyalty_discount`
- `shop/billing/subscription_service.py::SubscriptionService.calculate_total`
- `shop/billing/subscription_service.py::SubscriptionService.renew`
- `shop/api/renewal_controller.py::RenewalController.renew`
- `shop/billing/invoice_repository.py::InvoiceRepository.save`
- `shop/billing/tax_adapter.py::TaxAdapter.for_amount`
- plus the other callers in that neighborhood (tests, `deliver_retry`, `RenewalWebhook.handle`, `schedule_retry`, `Money`, `Loyalty`, `Subscription`, `Invoice`)

`ledger lake build` printed `backend: local-lake` (`DATABRICKS_HOST` and token unset). Databricks was not called.

| layer | file | rows |
|---|---|---:|
| bronze | `events.jsonl` | 1,807 |
| silver | `region_observations.jsonl` | 44 |
| gold | `working_set.jsonl` | 1,557 |
| gold | `bundles.jsonl` | 1 |
| gold | `metrics.jsonl` | 3 |

Silver is the join of `agent_tool` regions with `program_trace` regions onto `source_regions`. It includes `for_renewal` and `test_renewal_applies_loyalty_on_annual_boundary`. The three gold metric rows are `last_ab.json`, `last_eval.json`, and the audit (`largest_scc_size` 5, reverse BFS depth 3, seed side 21, other side `next_retry_hours`).

`ledger agentverse-demo --repo demo_repo` used a local round-trip (`transport: local-roundtrip`, `fixture: false`). It printed:

```
AGENTVERSE_API_KEY is unset; falling back to local mode. No mailbox was registered. The ASI:One submission form was not submitted.
local demo identity, not an Agentverse-registered mailbox: agent1qvntv3znytwfkn4u5zz9qsfekvw906l62k6hhg0e9xe3d6qx6s62cxaq2rq
bundle includes for_renewal in shop/billing/discount_policy.py
```

The written file is `demo_repo/.ledger/agentverse_demo.json`. That address is the public demo seed `ledger-runtime-local-demo-v1`. It is not an Agentverse-registered mailbox.

Dashboard: http://127.0.0.1:8765 (the process already bound to that port was restarted so it loaded `/api/lake`). Curl this session:

- `/api/health` → `ok: true`
- `/api/lake` → `backend: local-lake`, bronze 1807, silver 44, gold 1561 (working set 1557, bundles 1, metrics 3), `databricks_called: false`
- `/api/audit` → `fixture: false`, largest SCC 5, reverse BFS depth 3, seed side 21

The medallion strip is fed by `/api/lake`. The graph, hierarchy, and audit columns stay on the page. `python -m pytest tests -q`: 54 passed.

## Still needs a human

1. Claude login (`claude -p` is `Invalid API key`) before any live Claude A/B.
2. An Agentverse API key and a private `AGENT_SEED`, then `python -m ledger.agentverse --repo demo_repo --mailbox`.
3. The ASI:One Submission Agent form. This repository does not submit it.
4. A Databricks workspace, if you want a job to ingest `docs/LAKE.md`. No job was run.
