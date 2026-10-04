"""One GPT turn over a TraceWeaver bundle.

``OPENAI_API_KEY`` calls the OpenAI chat completions API.
Without a key, the turn is a local backup built by the controller.
``TRACEWEAVER_DUMMY=1`` returns the labeled fixture under ``traceweaver/fixtures/``
and never presents it as a live GPT or Claude measurement.
"""

from __future__ import annotations

import json
import os

from traceweaver.config import TraceWeaverConfig
from traceweaver.fixtures import load_fixture
from traceweaver.mcp.tools import render_bundle_text
from traceweaver.runtime import TraceWeaverRuntime


def _write(cfg: TraceWeaverConfig, payload: dict) -> dict:
    cfg.ensure_dirs()
    path = cfg.traceweaver_dir / "last_gpt_turn.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    payload = dict(payload)
    payload["written"] = str(path)
    return payload


def _bundle(cfg: TraceWeaverConfig, objective: str, seed: str | None) -> tuple[TraceWeaverRuntime, dict]:
    rt = TraceWeaverRuntime(cfg)
    rt.sync()
    sid = rt.ensure_session(agent="gpt-turn", condition="traceweaver")
    rt.set_objective(sid, objective)
    bundle = rt.controller.build_bundle(sid, objective=objective, seed=seed)
    return rt, bundle


def _regions(bundle: dict) -> list[dict]:
    out = []
    for entry in bundle.get("entries") or []:
        out.append(
            {
                "path": entry.get("path"),
                "symbol": entry.get("symbol"),
                "graph_tags": entry.get("graph_tags") or [],
                "score": entry.get("score"),
            }
        )
    return out


def gpt_turn(cfg: TraceWeaverConfig, objective: str, seed: str | None = None) -> dict:
    objective = (objective or "").strip()
    if not objective:
        raise ValueError("objective is required")

    if os.environ.get("TRACEWEAVER_DUMMY", "").strip() == "1":
        payload = load_fixture("gpt_trace.json")
        payload["fixture"] = True
        payload["live"] = False
        payload["source"] = "fixture"
        payload["provider"] = "fixture"
        payload["model"] = None
        payload["objective"] = objective
        payload["label"] = "FIXTURE GPT trace — not a live GPT measurement"
        payload["note"] = (
            "TRACEWEAVER_DUMMY=1. This trace is the file traceweaver/fixtures/gpt_trace.json. "
            "It is not a live OpenAI or Claude measurement."
        )
        return _write(cfg, payload)

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    rt, bundle = _bundle(cfg, objective, seed)
    regions = _regions(bundle)
    if api_key:
        try:
            live = _openai(api_key, objective, render_bundle_text(bundle))
        except Exception as exc:
            payload = {
                "fixture": False,
                "live": False,
                "source": "local_backup",
                "provider": "local",
                "model": None,
                "objective": objective,
                "label": "local backup — OpenAI call failed; not a live GPT measurement",
                "error": str(exc)[:400],
                "bundle_id": bundle.get("bundle_id"),
                "regions": regions,
                "text": render_bundle_text(bundle),
            }
            return _write(cfg, payload)
        payload = {
            "fixture": False,
            "live": True,
            "source": "openai",
            "provider": "openai",
            "model": live["model"],
            "objective": objective,
            "label": "live OpenAI chat completion over a TraceWeaver bundle",
            "bundle_id": bundle.get("bundle_id"),
            "regions": regions,
            "text": live["text"],
            "usage": live.get("usage"),
        }
        return _write(cfg, payload)

    payload = {
        "fixture": False,
        "live": False,
        "source": "local_backup",
        "provider": "local",
        "model": None,
        "objective": objective,
        "label": "local backup — OPENAI_API_KEY unset; not a live GPT measurement",
        "bundle_id": bundle.get("bundle_id"),
        "regions": regions,
        "text": render_bundle_text(bundle),
        "graph": {
            "algorithms": (bundle.get("graph") or {}).get("algorithms"),
            "seed": f"{(bundle.get('graph') or {}).get('seed_path')}::{(bundle.get('graph') or {}).get('seed_symbol')}",
        },
    }
    return _write(rt.cfg, payload)


def _openai(api_key: str, objective: str, bundle_text: str) -> dict:
    import httpx

    model = os.environ.get("TRACEWEAVER_OPENAI_MODEL", "gpt-4o-mini")
    response = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a coding assistant. Use the TraceWeaver context bundle. "
                        "Name the file and symbol you would edit. Be brief."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Objective: {objective}\n\nTraceWeaver bundle:\n{bundle_text[:12000]}",
                },
            ],
        },
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()
    text = data["choices"][0]["message"]["content"]
    return {"model": model, "text": text, "usage": data.get("usage")}


def read_last(cfg: TraceWeaverConfig) -> dict | None:
    path = cfg.traceweaver_dir / "last_gpt_turn.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
