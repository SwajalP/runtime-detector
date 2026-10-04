"""Strict token budget enforcement for a bundle.

Entries arrive in score order. If an entry would overflow the budget it is
compressed (exact → signature → summary) before being dropped, and pinned
entries are never dropped. Returns (entries, tokens_used).
"""

from __future__ import annotations

from ledger.context.representations import cost, render

_LEVELS = ["exact", "signature", "summary"]


def fit_budget(entries: list[dict], budget: int) -> tuple[list[dict], int]:
    kept: list[dict] = []
    used = 0
    for e in entries:
        e = dict(e)
        c = int(e.get("token_count") or cost(e.get("code") or ""))
        if used + c > budget and kept:
            # try progressively cheaper representations
            level_idx = _LEVELS.index(e.get("level", "exact")) if e.get("level") in _LEVELS else 0
            downgraded = False
            for level in _LEVELS[level_idx + 1 :]:
                text = render(e.get("_region") or {"body": e.get("code"), "signature": e.get("signature"), "symbol": e.get("symbol")}, level)
                c2 = cost(text)
                if used + c2 <= budget:
                    e["level"], e["code"], e["token_count"] = level, text, c2
                    e["compressed"] = True
                    c = c2
                    downgraded = True
                    break
            if not downgraded:
                if e.get("pinned"):
                    # pinned anchors always ship, as a summary at minimum
                    text = render(e.get("_region") or {"body": e.get("code"), "signature": e.get("signature"), "symbol": e.get("symbol")}, "summary")
                    e["level"], e["code"], e["token_count"], e["compressed"] = "summary", text, cost(text), True
                    c = e["token_count"]
                else:
                    e["dropped"] = True
                    continue
        e.pop("_region", None)
        kept.append(e)
        used += c
    return kept, used
