"""agentverse-demo writes a local reply and does not require a mailbox key."""

from __future__ import annotations

import json

from ledger.agentverse.demo import run_agentverse_demo
from ledger.agentverse.identity import demo_address


def test_agentverse_demo_falls_back_locally(demo_rt, monkeypatch):
    monkeypatch.delenv("AGENTVERSE_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_SEED", raising=False)
    report = run_agentverse_demo(demo_rt.cfg, roundtrip=False)
    assert report["fixture"] is False
    assert report["mailbox_registered"] is False
    assert report["asi_one_form_submitted"] is False
    assert report["live_model"] is False
    assert report["address"] == demo_address()
    assert report["includes_for_renewal"] is True
    blob = json.dumps(report["bundle"])
    assert "for_renewal" in blob
    assert "discount_policy.py" in blob
    notes = " ".join(report["notes"])
    assert "AGENTVERSE_API_KEY" in notes
    assert "falling back to local" in notes
    saved = json.loads((demo_rt.cfg.ledger_dir / "agentverse_demo.json").read_text(encoding="utf-8"))
    assert saved["fixture"] is False
    assert saved["includes_for_renewal"] is True
