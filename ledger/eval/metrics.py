"""Aggregation helpers for evaluation results."""

from __future__ import annotations

import statistics


def _median(values: list[float]) -> float | None:
    vals = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.median(vals), 3) if vals else None


def _mean(values: list[float]) -> float | None:
    vals = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.fmean(vals), 3) if vals else None


def aggregate(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    prefetched = sum(int(r.get("prefetches") or 0) for r in rows)
    prefetch_used = sum(int(r.get("prefetch_used") or 0) for r in rows)
    admitted = sum(int(r.get("admitted") or 0) for r in rows)
    pollution = sum(int(r.get("pollution") or 0) for r in rows)
    hits = sum(int(r.get("hits") or 0) for r in rows)
    misses = sum(int(r.get("misses") or 0) for r in rows)
    bundles = sum(int(r.get("bundles") or 0) for r in rows)
    fallback = sum(int(r.get("fallback_searches") or 0) for r in rows)
    return {
        "n": n,
        "success": sum(1 for r in rows if r.get("success")),
        "localized": sum(1 for r in rows if r.get("localized")),
        "repo_tool_calls": sum(int(r.get("repo_tool_calls") or 0) for r in rows),
        "ledger_tool_calls": sum(int(r.get("ledger_tool_calls") or 0) for r in rows),
        "total_tool_calls": sum(int(r.get("total_tool_calls") or 0) for r in rows),
        "repo_tokens": sum(int(r.get("repo_tokens") or 0) for r in rows),
        "agent_tool_tokens": sum(int(r.get("agent_tool_tokens") or 0) for r in rows),
        "ledger_injected_tokens": sum(int(r.get("ledger_injected_tokens") or 0) for r in rows),
        "files_read": sum(int(r.get("files_read") or 0) for r in rows),
        "median_time_to_target_s": _median([r.get("time_to_target_s") for r in rows]),
        "mean_calls_to_target": _mean([r.get("calls_to_target") for r in rows]),
        "mean_time_s": _mean([r.get("time_s") for r in rows]),
        "hit_rate": round(hits / (hits + misses), 3) if hits + misses else None,
        "prefetch_precision": round(prefetch_used / prefetched, 3) if prefetched else None,
        "pollution_rate": round(pollution / admitted, 3) if admitted else None,
        "stale_served": sum(int(r.get("stale_served") or 0) for r in rows),
        "invalidations": sum(int(r.get("invalidations") or 0) for r in rows),
        "evictions": sum(int(r.get("evictions") or 0) for r in rows),
        "fallback_rate": round(fallback / bundles, 3) if bundles else None,
    }


def per_task_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        b, l = r["baseline"], r["ledger"]
        bt, lt = int(b.get("repo_tokens") or 0), int(l.get("repo_tokens") or 0)
        out.append(
            {
                "task_id": r["task_id"],
                "family": r.get("family"),
                "held_out": r.get("held_out"),
                "repeat": r.get("repeat", 0),
                "baseline_success": bool(b.get("success")),
                "ledger_success": bool(l.get("success")),
                "baseline_calls": int(b.get("total_tool_calls") or 0),
                "ledger_calls": int(l.get("total_tool_calls") or 0),
                "baseline_tokens": bt,
                "ledger_tokens": lt,
                "tokens_change_pct": round((lt - bt) / bt * 100, 1) if bt else None,
                "baseline_time_to_target_s": b.get("time_to_target_s"),
                "ledger_time_to_target_s": l.get("time_to_target_s"),
                "ledger_prefetch_precision": l.get("prefetch_precision"),
                "ledger_hit_rate": l.get("hit_rate"),
            }
        )
    return out
