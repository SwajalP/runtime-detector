"""Region signature cards computed from indexed Tree-sitter (or AST) bodies.

A card is the signature line, the first docstring sentence, and a short callee
list. Byte and token totals are measured from rows already in ``source_regions``,
not from a second parse and not from a guessed ratio.
"""

from __future__ import annotations

import sqlite3

from ledger.events.schema import estimate_tokens


def _docstring_first_line(body: str) -> str:
    for line in (body or "").splitlines()[1:6]:
        s = line.strip()
        if s.startswith(('"""', "'''")):
            s = s.strip("\"'").strip()
            return s[:120]
    return ""


def signature_card(region: dict, callees: list[str] | None = None) -> str:
    """Compact card for one indexed region. Smaller than the stored body."""
    sig = (region.get("signature") or region.get("symbol") or "").strip()
    doc = _docstring_first_line(region.get("body") or "")
    lines = [sig] if sig else []
    if doc:
        lines.append(doc)
    names = [c.split(".")[-1] for c in (callees or []) if c][:6]
    if names:
        lines.append("calls " + " ".join(names))
    return "\n".join(lines)


def measure_cards(conn: sqlite3.Connection) -> dict:
    """Sum full-body bytes/tokens against signature-card bytes/tokens.

    Modules are omitted: their stored body repeats the functions inside them
    and is truncated at index time. Counts come from the index as it sits.
    """
    callees: dict[str, list[str]] = {}
    for row in conn.execute("SELECT caller_id, callee_name FROM calls"):
        callees.setdefault(row["caller_id"], []).append(row["callee_name"])

    full_body_bytes = card_bytes = 0
    full_body_tokens = card_tokens = 0
    n = 0
    for row in conn.execute(
        """
        SELECT region_id, path, symbol, kind, signature, body
        FROM source_regions
        WHERE kind != 'module'
        """
    ):
        region = dict(row)
        body = region.get("body") or ""
        card = signature_card(region, callees.get(region["region_id"], []))
        full_body_bytes += len(body.encode("utf-8"))
        card_bytes += len(card.encode("utf-8"))
        full_body_tokens += estimate_tokens(body)
        card_tokens += estimate_tokens(card)
        n += 1
    return {
        "regions": n,
        "full_body_bytes": full_body_bytes,
        "card_bytes": card_bytes,
        "full_body_tokens": full_body_tokens,
        "card_tokens": card_tokens,
        "source": "index",
    }


def format_card_line(stats: dict, *, backend: str) -> str:
    return (
        f"region signature cards (from index, {backend} bodies): "
        f"full-body bytes={stats['full_body_bytes']} "
        f"card bytes={stats['card_bytes']} "
        f"estimated tokens full-body={stats['full_body_tokens']} "
        f"estimated tokens cards={stats['card_tokens']} "
        f"regions={stats['regions']}"
    )
