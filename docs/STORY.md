# LEDGER Runtime

I'm Swajal Padhi. I built LEDGER Runtime for MHacks.

## What inspired us

Coding agents burn the first half of a task grepping and rereading. You hand one a failing test and it globs, opens a file, closes it, and searches again. The code that matters is a small working set. The rest of the window fills with files that share a few words with the bug.

CPUs already solved a similar shape. A working set is the pages you actually touch. Admission asks whether a miss costs more than bringing the page in. Prefetch pulls the neighbor you are about to need. Eviction drops what went cold. Freshness refuses a line after the bytes underneath it changed. I wanted that controller for source regions: functions and methods, under a token budget, sitting between the agent and its repository tools.

LEDGER records search, read, and edit events, joins them with the regions a targeted test executed, and serves a budgeted bundle. This is a logical context cache, not a model KV cache. Grep and read stay on the tool list. If confidence is low, the agent falls back to raw search.

## How we built it

The demo is a billing shop. Renewal invoices drop the loyalty discount on the annual boundary. The bug is in `for_renewal`. Promotions, legacy, and docs reuse the same words, so a plain search walks into those files on purpose.

The controller is Python. Tree-sitter splits the repository into regions. SQLite FTS does the lexical lookup. Pytest coverage maps executed lines back to functions. The rank of a region at a turn is

$$S(r,t) = \text{lexical} + \text{execution} + \text{structural} + \text{co-access} + \text{recency} + \text{diagnostic} - \text{token cost} - \text{staleness}$$

A region is admitted when this expected value is positive:

$$EV(r) = P(\text{used soon}) \cdot \text{miss cost} - \text{admit cost} - \text{pollution}$$

Past the token budget, the lowest keep-score is evicted first:

$$Keep(r) = \frac{\text{frequency} \cdot \text{recency} \cdot \text{utility}}{\text{token cost}}$$

Claude Code is the primary adapter: PreToolUse and PostToolUse hooks, plus an MCP server with `ledger_search`, `ledger_context`, and `ledger_explain`. Cline and GPT have tool configs too. With no API key, the GPT path uses a local backup. `ledger audit` runs reverse BFS from the failing test, Tarjan SCCs, and a Stoer-Wagner min-cut partition, then the controller packs a token-budgeted bundle. The dashboard shows the timeline, the working set, and that graph. A local Fetch.ai uAgent answers from the same controller on localhost.

## Challenges

Call edges initially resolved to the wrong method, so every SCC was size 1. The retry cycle in the shop was invisible until resolution was fixed. The demo shop also had a circular import. SQLite rejected a column named `commit` because that word is reserved. The pytest plugin loaded twice.

Claude login was invalid (`Invalid API key · Please run /login`), so the A/B is a hook-schema simulated agent, not a live model. The Agentverse mailbox needs a key, so the demo agent is local.

## What we learned

Dual traces beat either trace alone. A failing test's executed lines pinpoint the bug neighborhood, including `for_renewal`. Grep hits the lexical traps in legacy, promotions, and docs.

Prefetch that is too aggressive wastes tokens. The cap is two callees of a region that already scored high. On the simulated suite, five prefetches were issued and four were used later.

Time-to-target can get worse when you trace pytest first. Median time on that run moved from 0.4 s to 0.421 s, because LEDGER runs coverage-traced pytest before the program trace can join the agent trace. Calls to the target still fell from 2.167 to 1.0, since the region was already in the first bundle.

Success has to be held constant before you talk about efficiency. These numbers are from the simulated harness (`ledger eval --repo demo_repo --all`, 12 tasks, measured 2026-10-04): 12/12 both, search/read calls 203 to 8 (−96.1%), tokens 94,462 to 24,744 (−73.8%), counting injected context.

A judge can run `ledger ab`, `ledger audit`, and the local agent (`ledger agentverse-demo --repo demo_repo`).
