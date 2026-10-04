from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  task_id TEXT,
  agent TEXT NOT NULL,
  condition TEXT NOT NULL DEFAULT 'traceweaver',
  repo_id TEXT NOT NULL,
  commit_sha TEXT,
  started_ms INTEGER NOT NULL,
  ended_ms INTEGER,
  success INTEGER,
  label TEXT,
  objective TEXT
);

CREATE TABLE IF NOT EXISTS source_regions (
  region_id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL,
  commit_sha TEXT NOT NULL,
  path TEXT NOT NULL,
  symbol TEXT,
  kind TEXT NOT NULL,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  token_count INTEGER NOT NULL,
  signature TEXT,
  body TEXT,
  indexed_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_regions_path ON source_regions(path, start_line, end_line);
CREATE INDEX IF NOT EXISTS idx_regions_symbol ON source_regions(symbol);

CREATE VIRTUAL TABLE IF NOT EXISTS region_fts USING fts5(
  region_id UNINDEXED,
  path,
  symbol,
  signature,
  body,
  tokenize = 'porter'
);

CREATE TABLE IF NOT EXISTS calls (
  caller_id TEXT NOT NULL,
  callee_name TEXT NOT NULL,
  callee_id TEXT,
  PRIMARY KEY (caller_id, callee_name)
);

CREATE TABLE IF NOT EXISTS imports (
  region_id TEXT NOT NULL,
  module TEXT NOT NULL,
  name TEXT,
  PRIMARY KEY (region_id, module, name)
);

CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  task_id TEXT,
  turn INTEGER NOT NULL DEFAULT 0,
  timestamp_ms INTEGER NOT NULL,
  source TEXT NOT NULL,
  operation TEXT NOT NULL,
  query TEXT,
  regions_json TEXT NOT NULL DEFAULT '[]',
  latency_ms INTEGER,
  bytes_returned INTEGER,
  tokens_returned INTEGER,
  success INTEGER NOT NULL DEFAULT 1,
  extra_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, timestamp_ms);

CREATE TABLE IF NOT EXISTS working_set (
  session_id TEXT NOT NULL,
  region_id TEXT NOT NULL,
  last_access_turn INTEGER NOT NULL DEFAULT 0,
  access_frequency REAL NOT NULL DEFAULT 0,
  agent_access_score REAL NOT NULL DEFAULT 0,
  execution_score REAL NOT NULL DEFAULT 0,
  intent_score REAL NOT NULL DEFAULT 0,
  structural_score REAL NOT NULL DEFAULT 0,
  co_access_score REAL NOT NULL DEFAULT 0,
  edit_likelihood REAL NOT NULL DEFAULT 0,
  staleness REAL NOT NULL DEFAULT 0,
  token_cost REAL NOT NULL DEFAULT 0,
  observed_future_use REAL NOT NULL DEFAULT 0,
  pinned INTEGER NOT NULL DEFAULT 0,
  admitted INTEGER NOT NULL DEFAULT 0,
  stale INTEGER NOT NULL DEFAULT 0,
  explanation TEXT,
  replaced_by TEXT,
  PRIMARY KEY (session_id, region_id)
);

CREATE INDEX IF NOT EXISTS idx_ws_session ON working_set(session_id, execution_score DESC);

CREATE TABLE IF NOT EXISTS co_access (
  session_id TEXT NOT NULL,
  region_a TEXT NOT NULL,
  region_b TEXT NOT NULL,
  weight REAL NOT NULL,
  PRIMARY KEY (session_id, region_a, region_b)
);

CREATE TABLE IF NOT EXISTS bundles (
  bundle_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  objective TEXT,
  budget INTEGER NOT NULL,
  created_ms INTEGER NOT NULL,
  payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS controller_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  timestamp_ms INTEGER NOT NULL,
  action TEXT NOT NULL,
  region_id TEXT,
  detail_json TEXT NOT NULL DEFAULT '{}'
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path) -> sqlite3.Connection:
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


@contextmanager
def db_session(db_path: Path):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
