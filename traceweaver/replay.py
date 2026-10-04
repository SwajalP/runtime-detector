"""Session export and labelled replay (demo safety net, spec §15.3).

`export_session` writes the event log plus the latest bundle and working-set
snapshot. `replay_session` re-emits those into a *new* session marked
``agent='replay'`` with ``label`` starting ``REPLAY``. This is recorded
evidence, not a live model. Speed 0 skips inter-event sleeps (venue default).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from traceweaver.events.schema import RuntimeEvent, new_id, now_ms
from traceweaver.runtime import TraceWeaverRuntime

DEFAULT_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "replay" / "renewal-discount.jsonl"


def default_fixture() -> Path:
    return DEFAULT_FIXTURE


def _row(row) -> dict:
    return {k: row[k] for k in row.keys()} if row is not None else {}


def export_session(rt: TraceWeaverRuntime, session_id: str, out: Path) -> int:
    events = rt.collector.list_events(session_id, 100000)
    sess = rt.conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    bundle = rt.conn.execute(
        "SELECT * FROM bundles WHERE session_id = ? ORDER BY created_ms DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    working_set = [_row(r) for r in rt.conn.execute("SELECT * FROM working_set WHERE session_id = ?", (session_id,))]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"_session": _row(sess)}) + "\n")
        if bundle:
            fh.write(json.dumps({"_bundle": _row(bundle)}) + "\n")
        if working_set:
            fh.write(json.dumps({"_working_set": working_set}) + "\n")
        for e in events:
            fh.write(json.dumps(e) + "\n")
    return len(events)


def replay_session(rt: TraceWeaverRuntime, src: Path, speed: float = 0.0, max_gap_s: float = 1.5) -> str:
    lines = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    header: dict = {}
    bundle: dict | None = None
    working_set: list[dict] | None = None
    events: list[dict] = []
    for line in lines:
        if "_session" in line:
            header = line["_session"] or {}
        elif "_bundle" in line:
            bundle = line["_bundle"]
        elif "_working_set" in line:
            working_set = line["_working_set"]
        else:
            events.append(line)

    sid = rt.new_session(agent="replay", condition=header.get("condition", "traceweaver"), task_id=header.get("task_id"))
    label = f"REPLAY of {header.get('session_id') or 'fixture'}"
    rt.conn.execute("UPDATE sessions SET label = ? WHERE session_id = ?", (label, sid))
    prompt = rt.conn.execute(
        "SELECT extra_json FROM events WHERE session_id = ? AND operation = 'prompt' ORDER BY timestamp_ms LIMIT 1",
        (sid,),
    ).fetchone()
    extra = json.loads(prompt["extra_json"]) if prompt and prompt["extra_json"] else {}
    extra["replay"] = True
    extra["agent"] = "replay"
    rt.conn.execute(
        "UPDATE events SET extra_json = ? WHERE session_id = ? AND operation = 'prompt'",
        (json.dumps(extra), sid),
    )
    rt.conn.commit()

    if bundle:
        _restore_bundle(rt, sid, bundle)
    if working_set:
        _restore_working_set(rt, sid, working_set)

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


def _restore_bundle(rt: TraceWeaverRuntime, session_id: str, bundle: dict) -> None:
    raw = bundle.get("payload_json")
    payload = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    payload["session_id"] = session_id
    payload["replay"] = True
    bid = new_id("bnd")
    payload["bundle_id"] = bid
    rt.conn.execute(
        "INSERT INTO bundles(bundle_id, session_id, objective, budget, created_ms, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
        (
            bid,
            session_id,
            bundle.get("objective") or payload.get("objective"),
            int(bundle.get("budget") or payload.get("budget") or 0),
            now_ms(),
            json.dumps(payload),
        ),
    )
    rt.conn.commit()


def _restore_working_set(rt: TraceWeaverRuntime, session_id: str, rows: list[dict]) -> None:
    cols = (
        "session_id", "region_id", "last_access_turn", "access_frequency",
        "agent_access_score", "execution_score", "intent_score", "structural_score",
        "co_access_score", "edit_likelihood", "staleness", "token_cost",
        "observed_future_use", "pinned", "admitted", "stale", "explanation", "replaced_by",
    )
    for row in rows:
        values = []
        for col in cols:
            if col == "session_id":
                values.append(session_id)
            else:
                values.append(row.get(col))
        placeholders = ", ".join("?" * len(cols))
        rt.conn.execute(
            f"INSERT OR REPLACE INTO working_set ({', '.join(cols)}) VALUES ({placeholders})",
            values,
        )
    rt.conn.commit()
