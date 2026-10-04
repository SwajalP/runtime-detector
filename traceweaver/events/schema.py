from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

EventSource = Literal["agent_tool", "program_trace", "edit", "diagnostic", "controller"]
Operation = Literal[
    "glob",
    "grep",
    "read",
    "definition",
    "references",
    "test",
    "execute",
    "edit",
    "admit",
    "hit",
    "miss",
    "evict",
    "invalidate",
    "prefetch",
    "bundle_served",
    "prompt",
    "complete",
]


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def region_id_for(
    repo_id: str,
    commit: str,
    path: str,
    start: int,
    end: int,
    content_hash: str,
) -> str:
    raw = f"{repo_id}|{commit}|{path}|{start}|{end}|{content_hash}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


@dataclass
class SourceRegion:
    region_id: str
    repo_id: str
    commit_sha: str
    path: str
    symbol: str | None
    kind: str
    start_line: int
    end_line: int
    content_hash: str
    token_count: int
    signature: str | None = None
    body: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeEvent:
    event_id: str
    session_id: str
    task_id: str | None
    turn: int
    timestamp_ms: int
    source: str
    operation: str
    query: str | None = None
    regions: list[str] = field(default_factory=list)
    latency_ms: int | None = None
    bytes_returned: int | None = None
    tokens_returned: int | None = None
    success: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "turn": self.turn,
            "timestamp_ms": self.timestamp_ms,
            "source": self.source,
            "operation": self.operation,
            "query": self.query,
            "regions_json": json.dumps(self.regions),
            "latency_ms": self.latency_ms,
            "bytes_returned": self.bytes_returned,
            "tokens_returned": self.tokens_returned,
            "success": 1 if self.success else 0,
            "extra_json": json.dumps(self.extra),
        }


def event_from_row(row: sqlite3_row_like) -> dict[str, Any]:
    d = dict(row)
    d["regions"] = json.loads(d.pop("regions_json") or "[]")
    d["extra"] = json.loads(d.pop("extra_json") or "{}")
    d["success"] = bool(d.get("success"))
    return d


# typing helper
sqlite3_row_like = Any
