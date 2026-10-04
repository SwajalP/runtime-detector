from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from threading import Lock

from ledger.events.schema import RuntimeEvent, event_from_row, new_id, now_ms


class EventCollector:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._lock = Lock()
        self._listeners: list[Callable[[dict], None]] = []
        self._turn: dict[str, int] = {}

    def add_listener(self, fn: Callable[[dict], None]) -> None:
        self._listeners.append(fn)

    def current_turn(self, session_id: str) -> int:
        return self._turn.get(session_id, 0)

    def bump_turn(self, session_id: str) -> int:
        self._turn[session_id] = self._turn.get(session_id, 0) + 1
        return self._turn[session_id]

    def emit(self, event: RuntimeEvent) -> dict:
        with self._lock:
            if event.turn == 0:
                event.turn = self._turn.get(event.session_id, 0)
            row = event.to_row()
            self.conn.execute(
                """
                INSERT INTO events (
                  event_id, session_id, task_id, turn, timestamp_ms, source,
                  operation, query, regions_json, latency_ms, bytes_returned,
                  tokens_returned, success, extra_json
                ) VALUES (
                  :event_id, :session_id, :task_id, :turn, :timestamp_ms, :source,
                  :operation, :query, :regions_json, :latency_ms, :bytes_returned,
                  :tokens_returned, :success, :extra_json
                )
                """,
                row,
            )
            self.conn.commit()
        payload = {
            "event_id": event.event_id,
            "session_id": event.session_id,
            "task_id": event.task_id,
            "turn": event.turn,
            "timestamp_ms": event.timestamp_ms,
            "source": event.source,
            "operation": event.operation,
            "query": event.query,
            "regions": event.regions,
            "latency_ms": event.latency_ms,
            "bytes_returned": event.bytes_returned,
            "tokens_returned": event.tokens_returned,
            "success": event.success,
            "extra": event.extra,
        }
        for listener in list(self._listeners):
            try:
                listener(payload)
            except Exception:
                pass
        return payload

    def record(
        self,
        *,
        session_id: str,
        source: str,
        operation: str,
        task_id: str | None = None,
        query: str | None = None,
        regions: list[str] | None = None,
        latency_ms: int | None = None,
        bytes_returned: int | None = None,
        tokens_returned: int | None = None,
        success: bool = True,
        extra: dict | None = None,
        bump: bool = False,
    ) -> dict:
        if bump:
            self.bump_turn(session_id)
        event = RuntimeEvent(
            event_id=new_id("evt"),
            session_id=session_id,
            task_id=task_id,
            turn=self.current_turn(session_id),
            timestamp_ms=now_ms(),
            source=source,
            operation=operation,
            query=query,
            regions=regions or [],
            latency_ms=latency_ms,
            bytes_returned=bytes_returned,
            tokens_returned=tokens_returned,
            success=success,
            extra=extra or {},
        )
        return self.emit(event)

    def list_events(self, session_id: str | None = None, limit: int = 500) -> list[dict]:
        if session_id:
            rows = self.conn.execute(
                "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp_ms ASC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM events ORDER BY timestamp_ms DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [event_from_row(r) for r in rows]
