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

`ledger index` (Tree-sitter bodies already in the index, 70 non-module regions):

| | bytes | estimated tokens |
|---|---:|---:|
| full body | 18,548 | 4,610 |
| signature card | 5,824 | 1,430 |

`LEDGER_DUMMY=1` wrote `demo_repo/.ledger/last_gpt_turn.json` with `fixture: true`, `provider: "fixture"`, `live: false`. That file is `ledger/fixtures/gpt_trace.json`. It is not an OpenAI call.

The next `ledger gpt-turn` (no dummy, no API key) was `source: local_backup`, `fixture: false`, `live: false`. The bundle included `shop/billing/discount_policy.py::for_renewal` with graph tags `reverse_bfs`, `min_cut`, `tarjan`.

`ledger audit` wrote `demo_repo/.ledger/last_audit.json`. Structural, not a SonarQube score:

- large SCCs: 0 (largest component size 1)
- Stoer–Wagner min-cut around `shop/billing/discount_policy.py::for_renewal`: weight 1.0, 8 nodes
- clone clusters: 0
- lexical traps: 30 (legacy, promotions, and other renewal/discount lookalikes)
- `fixture_comparison` and `mailbox_status` are labeled `fixture: true`

`ledger ab --task renewal-discount` wrote `demo_repo/.ledger/last_ab.json`. Hook schema, not live Claude (`claude -p` returned `Invalid API key · Please run /login`):

| condition | target found | advice | repo calls | ledger calls | repo tokens |
|---|---|---:|---:|---:|---:|
| baseline | yes | 0 | 20 | 0 | 9,103 |
| ledger | yes | 1 | 4 | 3 | 4,740 |

The dashboard at http://127.0.0.1:8765 loaded. The structural-audit panel rendered the real `last_audit.json` (`fixture: false`).

The local client reply named `for_renewal` in `shop/billing/discount_policy.py`. Identity (seed `ledger-runtime-local-demo-v1`, not a registered mailbox): `agent1qvntv3znytwfkn4u5zz9qsfekvw906l62k6hhg0e9xe3d6qx6s62cxaq2rq`.

`python -m pytest tests -q`: 49 passed.
