"""Local vector index over source-region signature cards.

Chunks are existing source regions (function, method, class), not whole files.
The embedded text is the card: symbol, signature, first docstring line, and calls.

The embedder is a deterministic hashed bag-of-tokens (256-d float32, L2-normalized),
model name ``local-hash``. Vectors are stored in SQLite table ``region_vectors``
and ranked with brute-force cosine. Backend name is ``sqlite-cosine``.
"""

from __future__ import annotations

import hashlib
import math
import sqlite3
import struct
import time

from traceweaver.config import TraceWeaverConfig
from traceweaver.index.cards import signature_card
from traceweaver.index.lexical import tokenize

DIM = 256
COLLECTION = "source_regions"
MODEL = "local-hash"
BACKEND = "sqlite-cosine"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS region_vectors (
  region_id TEXT PRIMARY KEY,
  dim INTEGER NOT NULL,
  vector BLOB NOT NULL,
  model TEXT NOT NULL,
  updated_ms INTEGER NOT NULL
)
"""

_OPEN: dict[str, "VectorIndex"] = {}


def hash_embed(text: str) -> list[float]:
    """Signed feature hash. The same tokens always produce the same unit vector."""
    vec = [0.0] * DIM
    tokens = tokenize(text)
    if not tokens:
        vec[0] = 1.0
        return vec
    for tok in tokens:
        digest = hashlib.sha256(tok.encode("utf-8")).digest()
        slot = int.from_bytes(digest[:4], "little") % DIM
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[slot] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        vec[0] = 1.0
        return vec
    return [v / norm for v in vec]


def region_card(region: dict) -> str:
    symbol = (region.get("symbol") or "").strip()
    card = signature_card(region, region.get("callees"))
    if symbol and symbol not in card:
        return f"{symbol}\n{card}" if card else symbol
    return card or symbol


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes, dim: int) -> list[float]:
    return list(struct.unpack(f"{dim}f", blob))


class VectorIndex:
    def __init__(self, cfg: TraceWeaverConfig, conn: sqlite3.Connection | None = None):
        self.cfg = cfg
        self.conn = conn
        self.model = MODEL
        self.backend = BACKEND
        self.dimension = DIM
        self.path = str(cfg.db_path)
        self._query_cache: dict[str, dict[str, float]] = {}

    def _ensure_table(self) -> None:
        if self.conn is None:
            raise RuntimeError("sqlite-cosine store needs a database connection")
        self.conn.execute(_SCHEMA)
        self.conn.commit()

    def _has_table(self) -> bool:
        if self.conn is None:
            return False
        return (
            self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='region_vectors'"
            ).fetchone()
            is not None
        )

    def upsert(self, regions: list[dict]) -> int:
        if self.conn is None:
            raise RuntimeError("sqlite-cosine store needs a database connection")
        self._query_cache.clear()
        self._ensure_table()
        now = int(time.time() * 1000)
        n = 0
        for region in regions:
            rid = region.get("region_id")
            if not rid or region.get("kind") == "module":
                continue
            vec = hash_embed(region_card(region))
            self.conn.execute(
                """
                INSERT INTO region_vectors (region_id, dim, vector, model, updated_ms)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(region_id) DO UPDATE SET
                  dim=excluded.dim,
                  vector=excluded.vector,
                  model=excluded.model,
                  updated_ms=excluded.updated_ms
                """,
                (rid, DIM, _pack(vec), self.model, now),
            )
            n += 1
        self.conn.commit()
        return n

    def _rows(self) -> list[tuple[str, list[float]]]:
        if self.conn is None or not self._has_table():
            return []
        rows = self.conn.execute("SELECT region_id, dim, vector FROM region_vectors").fetchall()
        return [(row["region_id"], _unpack(row["vector"], int(row["dim"]))) for row in rows]

    def _scores(self, text: str) -> dict[str, float]:
        cached = self._query_cache.get(text)
        if cached is not None:
            return cached
        query = hash_embed(text)
        scored = {rid: _dot(query, vec) for rid, vec in self._rows()}
        self._query_cache[text] = scored
        return scored

    def query(self, text: str, k: int = 5) -> list[dict]:
        ranked = sorted(self._scores(text).items(), key=lambda item: item[1], reverse=True)[: max(k, 0)]
        return [{"region_id": rid, "cosine": round(_clip01(cos), 4)} for rid, cos in ranked]

    def cosine_for(self, region_id: str, text: str) -> float | None:
        if self.count() == 0:
            return None
        return self._scores(text).get(region_id, 0.0)

    def count(self) -> int:
        if self.conn is None or not self._has_table():
            return 0
        return int(self.conn.execute("SELECT COUNT(*) c FROM region_vectors").fetchone()["c"])

    def status(self) -> dict:
        return {
            "backend": self.backend,
            "path": self.path,
            "collection": COLLECTION,
            "dimension": self.dimension,
            "count": self.count(),
            "model": self.model,
        }


def open_index(cfg: TraceWeaverConfig, conn: sqlite3.Connection | None = None) -> VectorIndex:
    key = str(cfg.traceweaver_dir)
    idx = _OPEN.get(key)
    if idx is None:
        idx = VectorIndex(cfg, conn)
        _OPEN[key] = idx
    elif conn is not None:
        idx.conn = conn
    return idx


def embed_regions(cfg: TraceWeaverConfig, conn: sqlite3.Connection) -> dict:
    """Embed every non-module source region and return vector-store status."""
    callees: dict[str, list[str]] = {}
    try:
        for row in conn.execute("SELECT caller_id, callee_name FROM calls"):
            callees.setdefault(row["caller_id"], []).append(row["callee_name"])
    except sqlite3.OperationalError:
        callees = {}
    regions = []
    for row in conn.execute("SELECT * FROM source_regions WHERE kind != 'module'"):
        region = dict(row)
        region["callees"] = callees.get(region["region_id"], [])
        regions.append(region)
    idx = open_index(cfg, conn)
    idx.upsert(regions)
    return idx.status()


def vector_status(cfg: TraceWeaverConfig, conn: sqlite3.Connection | None = None) -> dict:
    return open_index(cfg, conn).status()
