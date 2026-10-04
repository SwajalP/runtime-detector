"""Local medallion lake over the append-only LEDGER event log.

Bronze, silver, and gold are JSONL files under ``.ledger/lake/``. Nothing
here talks to Databricks. See ``docs/LAKE.md`` for the table layout a
Databricks job could ingest.
"""

from ledger.lake.pipeline import build_lake, lake_summary, savings_from_latest

__all__ = ["build_lake", "lake_summary", "savings_from_latest"]
