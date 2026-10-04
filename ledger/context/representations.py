"""Budget-aware representation selection.

High-value regions ship as exact code; mid-value neighbours as signatures;
low-value neighbours as one-line summaries with call edges. This is the
"compression level" of a cache line, implemented as plain text selection.
"""

from __future__ import annotations

from ledger.events.schema import estimate_tokens


def represent(score: float, token_count: int, pinned: bool = False) -> str:
    """Pick exact / signature / summary from value and size.

    Exact code is reserved for anchors and high-value regions; mid-value regions
    ship as exact only when tiny; low-value neighbours are one-line summaries.
    """
    if pinned or score >= 0.55:
        return "exact"
    if score >= 0.42:
        return "exact" if token_count < 120 else "signature"
    if score >= 0.30:
        return "signature"
    return "summary"


def render(region: dict, level: str, callees: list[str] | None = None) -> str:
    body = region.get("body") or ""
    sig = region.get("signature") or (body.splitlines()[0].strip() if body else region.get("symbol") or "")
    if level == "exact":
        return body
    if level == "signature":
        doc = _docstring_first_line(body)
        return sig + (f"\n    \"\"\"{doc}\"\"\"" if doc else "") + "\n    ..."
    # summary
    edges = f"  # calls: {', '.join(callees[:4])}" if callees else ""
    return f"{sig}  # summarized neighbor{edges}"


def _docstring_first_line(body: str) -> str:
    lines = body.splitlines()
    for i, line in enumerate(lines[1:4], start=1):
        s = line.strip()
        if s.startswith(('"""', "'''")):
            s = s.strip('"\'').strip()
            return s[:100]
    return ""


def cost(text: str) -> int:
    return estimate_tokens(text)
