"""Cost-aware decayed-LFU replacement (spec §8.4).

Keep(r) = frequency · recency · utility / token_cost

Pinned anchors (current diff, failing assertion, top exception frame) are never
evicted by the generic policy. Everything else is dropped lowest-Keep-first
until the bundle fits the budget.
"""

from __future__ import annotations

import math


def keep_score(ws: dict, token_count: int, turn: int = 0) -> float:
    if int(ws.get("pinned") or 0):
        return 1e9
    freq = float(ws.get("access_frequency") or 0) + 0.1
    last = float(ws.get("last_access_turn") or 0)
    recency = math.exp(-0.25 * max(0.0, turn - last)) if last else 0.35
    utility = (
        0.4 * float(ws.get("execution_score") or 0)
        + 0.3 * float(ws.get("intent_score") or 0)
        + 0.2 * float(ws.get("agent_access_score") or 0)
        + 0.1 * float(ws.get("co_access_score") or 0)
    )
    cost = max(float(token_count or 1), 1.0)
    return (freq * recency * (0.15 + utility)) / cost * 100.0


def evict_to_budget(items: list[dict], budget: int) -> tuple[list[dict], list[dict]]:
    """Return (kept, evicted). ``items`` carry region, score_info, keep, pinned."""
    pinned = [i for i in items if i.get("pinned")]
    others = sorted((i for i in items if not i.get("pinned")), key=lambda x: (x["keep"], x["score_info"]["score"]), reverse=True)
    kept: list[dict] = list(pinned)
    used = sum(int(i["region"].get("token_count") or 0) for i in pinned)
    evicted: list[dict] = []
    for item in others:
        cost = int(item["region"].get("token_count") or 0)
        if used + cost <= budget or not kept:
            kept.append(item)
            used += cost
        else:
            evicted.append(item)
    kept.sort(key=lambda x: (x.get("pinned", False), x["score_info"]["score"]), reverse=True)
    return kept, evicted


def lru_evict(items: list[dict], budget: int) -> tuple[list[dict], list[dict]]:
    """Baseline comparator: pure recency ordering (used by `ledger policy-compare`)."""
    ordered = sorted(items, key=lambda x: float(x.get("ws", {}).get("last_access_turn") or 0), reverse=True)
    kept, evicted, used = [], [], 0
    for item in ordered:
        cost = int(item["region"].get("token_count") or 0)
        if item.get("pinned") or used + cost <= budget or not kept:
            kept.append(item)
            used += cost
        else:
            evicted.append(item)
    return kept, evicted
