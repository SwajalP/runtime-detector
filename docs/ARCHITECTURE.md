# TraceWeaver Runtime architecture

TraceWeaver is an online context-control layer between a coding agent and repository tools. It is a **logical cache of source regions**, not a model-provider KV cache.

```
agent ──► hooks / MCP / Agentverse / CLI
              │
              ▼
        TraceWeaverRuntime
         ├─ EventCollector   agent_tool + program_trace + controller ops
         ├─ incremental index  tree-sitter regions + FTS + call edges
         ├─ ContextController  score → admit → represent → budget → prefetch
         └─ SQLite store       sessions, working_set, bundles, co_access
              │
              ▼
        FastAPI dashboard     /api/state  /api/graph  /api/graph/hierarchy  /api/lake
              │
              ▼
        local medallion lake  bronze events → silver region observations → gold
                              working set, latest bundle, audit, A/B metrics
```

`traceweaver lake build` reads the SQLite log and writes JSONL under `.traceweaver/lake/`. That is a local pipeline. It does not call Databricks. The table layout a Databricks job would ingest is `docs/LAKE.md`.

## Dual trace

1. **Agent trace** — Pre/Post tool hooks (Claude Code) or the simulated harness record grep/read/edit/test events and the regions they touched.
2. **Program trace** — `traceweaver test` / Agentverse `TraceRequest` runs pytest under coverage and maps executed lines onto the same region ids. Failing-test frames are pinned as L0 anchors.

The controller joins the two traces: a region that was both searched and executed ranks above a region that only appeared in docs or marketing copy.

## Memory hierarchy

| Level | What lives there | Bound |
|---|---|---|
| L0 | Pinned anchors: failing frames, edited regions | pin set |
| L1 | Current session bundle (exact / summary / signature) | `token_budget` (default 2400) |
| L2 | Working-set + co-access memory on this repo | unbounded, decayed |
| backing | Every indexed region, hash-versioned | the repository |

Admission is `expected_value(score, tokens)` against `admission_threshold`. Replacement is `Keep(r)` under the token budget. Exact code is served only after a content-hash check against disk; a mismatch invalidates the path and is never counted as a hit (`stale_served` target is 0).

## Agent-facing tools

The same controller is exposed three ways:

- MCP stdio: `traceweaver_search`, `traceweaver_context`, `traceweaver_explain`
- CLI: `traceweaver context`, `traceweaver explain`, `traceweaver test`, `traceweaver eval`, `traceweaver lake build`
- Fetch.ai uAgent: `TraceWeaverContextProtocol` + chat (`traceweaver agentverse-demo`)

Raw Grep / Read / Bash / Edit stay available. TraceWeaver annotates repeated broad search; it does not hide the repository.

## Evaluation

`traceweaver eval --repo demo_repo` runs a fixed simulated agent twice (baseline vs TraceWeaver) on the same commit and the same pytest selection. TraceWeaver's own injected tokens and tool calls are counted against TraceWeaver. Success is reported before efficiency. Held-out tasks (`held-out-proration`, `held-out-dunning`) are not used to tune weights.
