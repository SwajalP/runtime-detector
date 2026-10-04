"""Replay is labelled REPLAY and is not a live model run."""

from __future__ import annotations

from traceweaver.eval.runner import load_tasks, run_task
from traceweaver.replay import default_fixture, export_session, replay_session


def test_export_and_replay_are_labelled_replay(demo_rt, tmp_path):
    tasks = {t["id"]: t for t in load_tasks(demo_rt.cfg.repo_root)}
    result = run_task(demo_rt, tasks["renewal-discount"], use_traceweaver=True)
    assert result["success"]
    src_sid = demo_rt.conn.execute("SELECT session_id FROM sessions ORDER BY started_ms DESC LIMIT 1").fetchone()[
        "session_id"
    ]

    out = tmp_path / "renewal-discount.jsonl"
    n = export_session(demo_rt, src_sid, out)
    assert n > 0
    text = out.read_text(encoding="utf-8")
    assert '"_session"' in text
    assert '"_bundle"' in text
    assert '"_working_set"' in text

    replayed = replay_session(demo_rt, out, speed=0)
    assert replayed != src_sid
    row = demo_rt.conn.execute("SELECT * FROM sessions WHERE session_id = ?", (replayed,)).fetchone()
    assert row["agent"] == "replay"
    assert str(row["label"]).startswith("REPLAY")
    assert row["task_id"] == "renewal-discount"

    events = demo_rt.collector.list_events(replayed, 10000)
    assert events
    assert all(e.get("extra", {}).get("replay") is True for e in events)

    bundle = demo_rt.conn.execute(
        "SELECT payload_json FROM bundles WHERE session_id = ? ORDER BY created_ms DESC LIMIT 1",
        (replayed,),
    ).fetchone()
    assert bundle
    import json

    payload = json.loads(bundle["payload_json"])
    assert payload.get("replay") is True
    assert payload.get("session_id") == replayed
    assert payload.get("entries")

    ws = demo_rt.controller.working_set(replayed)
    assert ws
    state = demo_rt.dashboard_state(replayed)
    assert state["bundle"]
    assert state["working_set"]
    assert state["replay"] is True
    assert str(state.get("session_label") or "").startswith("REPLAY")


def test_committed_fixture_replays_as_replay_not_live(demo_rt):
    path = default_fixture()
    assert path.is_file(), f"missing venue fixture {path}"
    text = path.read_text(encoding="utf-8")
    assert '"_session"' in text
    assert '"_bundle"' in text
    sid = replay_session(demo_rt, path, speed=0)
    row = demo_rt.conn.execute("SELECT * FROM sessions WHERE session_id = ?", (sid,)).fetchone()
    assert row["agent"] == "replay"
    assert str(row["label"]).startswith("REPLAY")
    events = demo_rt.collector.list_events(sid, 10000)
    assert any(e["operation"] == "bundle_served" for e in events)
    assert all(e.get("extra", {}).get("replay") is True for e in events)
    state = demo_rt.dashboard_state(sid)
    assert state["replay"] is True
    assert state["bundle"]
    assert state["bundle"].get("replay") is True
