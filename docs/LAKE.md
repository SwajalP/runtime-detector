# Local medallion lake

LEDGER stores an append-only event log in SQLite (`events`). `ledger lake build` materializes that log as bronze, silver, and gold JSONL on disk. The job runs in-process. It does not call Databricks, and it does not report a Databricks benchmark.

When `DATABRICKS_HOST` and `DATABRICKS_TOKEN` are unset, the command prints `backend: local-lake` and still writes the files. When they are set, it still does not call Databricks. A Databricks job can ingest the paths below; this repository does not run that job.

## Paths

All paths are relative to the repository `.ledger` directory (`demo_repo/.ledger` for the demo):

| Layer | File | Grain |
|---|---|---|
| bronze | `lake/bronze/events.jsonl` | one raw event |
| silver | `lake/silver/region_observations.jsonl` | one agent-tool hit of a region that a program trace also observed |
| gold | `lake/gold/working_set.jsonl` | one working-set row |
| gold | `lake/gold/bundles.jsonl` | the latest bundle |
| gold | `lake/gold/metrics.jsonl` | one A/B or audit row |

`lake/manifest.json` records counts and `databricks_called: false`.

Bronze lines already on disk are not rewritten. New events are appended. The SQLite `events` table is only read. Silver and gold are rebuilt from the current database each time, because they are derived tables.

Secrets: silver and gold pass free text through `ledger.events.redact`. Paths matching the secret globs are dropped. Bronze keeps the raw event row.

## Bronze schema (`events.jsonl`)

| column | type |
|---|---|
| event_id | string |
| session_id | string |
| task_id | string |
| turn | int |
| timestamp_ms | long |
| source | string (`agent_tool`, `program_trace`, `controller`, `edit`, `diagnostic`) |
| operation | string |
| query | string |
| regions | array of region id strings |
| latency_ms | int |
| bytes_returned | int |
| tokens_returned | int |
| success | bool |
| extra | object |

## Silver schema (`region_observations.jsonl`)

Inner join of `agent_tool` events to the set of region ids referenced by `program_trace` events, then to `source_regions`.

| column | type |
|---|---|
| event_id | string |
| session_id | string |
| task_id | string |
| timestamp_ms | long |
| operation | string |
| region_id | string |
| path | string |
| symbol | string |
| content_hash | string |
| commit | string (from `source_regions.commit_sha`) |
| kind | string |
| query | string (redacted) |
| program_traced | bool |

## Gold schemas

`working_set.jsonl`: `session_id`, `region_id`, `path`, `symbol`, `content_hash`, `commit`, `admitted`, `pinned`, `stale`, `execution_score`, `agent_access_score`, `token_cost`, `explanation`, `materialized_ms`.

`bundles.jsonl`: one row, `latest: true`. `bundle_id`, `session_id`, `objective`, `budget`, `token_count`, `entries[]` (`region_id`, `path`, `symbol`, `content_hash`, `start_line`, `end_line`). Entry bodies are not copied.

`metrics.jsonl`:

- `kind: "ab"` or `kind: "eval"` from `last_ab.json` / `last_eval.json` when that file exists and is not `fixture: true`. Columns: `source_file`, `baseline_repo_calls`, `ledger_repo_calls`, `call_change_pct`, `baseline_repo_tokens`, `ledger_repo_tokens`, `token_change_pct`.
- `kind: "audit"` from the index: `largest_scc_size`, `large_sccs`, `reverse_bfs_depth`, `reverse_bfs_path`, `min_cut_weight`, `min_cut_seed_side`, `min_cut_seed_side_count`, `min_cut_other_side`. `source_file` is `index`. `fixture` is false.

`ledger lake show` prints row counts and the call/token deltas from the newer of `last_eval.json` and `last_ab.json`, labeled with that filename.

## What a Databricks job would read

A workspace with these files mounted (or copied) could define three schemas and one job. This is the layout, not a job that has been run:

```text
bronze.events              <- lake/bronze/events.jsonl
silver.region_observations <- lake/silver/region_observations.jsonl
gold.working_set           <- lake/gold/working_set.jsonl
gold.bundles               <- lake/gold/bundles.jsonl
gold.metrics               <- lake/gold/metrics.jsonl
```

Example read (Spark SQL types follow the columns above; do not treat this block as an executed job):

```sql
CREATE TABLE IF NOT EXISTS bronze.events
USING json
LOCATION '<mount>/lake/bronze/events.jsonl';
```

Repeat for the silver and gold paths. No `DATABRICKS_HOST` call is made by `ledger lake build`.
