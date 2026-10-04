"""Session export and labelled replay (demo safety net, spec §15.3).

`export_session` writes the append-only event log of a session to JSONL.
`replay_session` re-emits those events into a *new* session marked
``agent='replay'`` with the original inter-event timing (optionally scaled) so
the dashboard animates exactly what was recorded. Replays are always labelled.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ledger.events.schema import RuntimeEvent, new_id, now_ms
from ledger.runtime import LedgerRuntime


def export_session(rt: LedgerRuntime, session_id: str, out: Path) -> int:
    events = rt.collector.list_events(session_id, 100000)
    sess = rt.conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"_session": dict(sess)}) + "\n")
        for e in events:
            fh.write(json.dumps(e) + "\n")
    return len(events)


def replay_session(rt: LedgerRuntime, src: Path, speed: float = 1.0, max_gap_s: float = 1.5) -> str:
    lines = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    header = lines[0].get("_session", {}) if lines and "_session" in lines[0] else {}
    events = [l for l in lines if "_session" not in l]
    sid = rt.new_session(agent="replay", condition=header.get("condition", "ledger"), task_id=header.get("task_id"))
    rt.conn.execute("UPDATE sessions SET label = ? WHERE session_id = ?", (f"REPLAY of {header.get('session_id','?')}", sid))
    rt.conn.commit()
    prev_ts = None
    for e in events:
        ts = int(e.get("timestamp_ms") or 0)
        if prev_ts is not None and speed > 0:
            gap = min(max_gap_s, max(0.0, (ts - prev_ts) / 1000.0 / speed))
            time.sleep(gap)
        prev_ts = ts
        extra = dict(e.get("extra") or {})
        extra["replay"] = True
        rt.collector.emit(
            RuntimeEvent(
                event_id=new_id("rpl"),
                session_id=sid,
                task_id=e.get("task_id"),
                turn=int(e.get("turn") or 0),
                timestamp_ms=now_ms(),
                source=e.get("source", "controller"),
                operation=e.get("operation", "execute"),
                query=e.get("query"),
                regions=list(e.get("regions") or []),
                latency_ms=e.get("latency_ms"),
                bytes_returned=e.get("bytes_returned"),
                tokens_returned=e.get("tokens_returned"),
                success=bool(e.get("success", True)),
                extra=extra,
            )
        )
    rt.end_session(sid, header.get("success"))
    return sid
