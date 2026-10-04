"""Login-free hook A/B finds the renewal-discount target on the traceweaver side."""

from __future__ import annotations

import json
from pathlib import Path

from traceweaver.adapters.claude_ab import run_ab
from traceweaver.eval.runner import load_tasks


def test_prompt_file_matches_task():
    text = Path(__file__).resolve().parents[1].joinpath("docs/prompts/renewal-discount.md").read_text()
    task = next(t for t in load_tasks(Path(__file__).resolve().parents[1] / "demo_repo") if t["id"] == "renewal-discount")
    assert task["prompt"].strip() in text


def test_hook_ab_finds_target_on_traceweaver_side(demo_rt):
    report = run_ab(demo_rt.cfg, "renewal-discount", attempt_claude=False)
    assert report["source"] == "claude_hooks"
    assert report["claude"]["ran"] is False
    assert report["baseline"]["advice_count"] == 0
    assert report["baseline"]["observe_only"] is True
    assert report["baseline"]["session_advice"] is False
    assert report["traceweaver"]["target_found"] is True
    assert "shop/billing/discount_policy.py" in report["traceweaver"]["paths_seen"]
    assert report["traceweaver"]["advice_count"] >= 1 or report["traceweaver"]["session_advice"] is True
    written = json.loads(Path(report["written"]).read_text())
    assert written["traceweaver"]["target_found"] is True
    assert written["claude"]["ran"] is False
