"""Invoice CSV/JSON export for finance.

Consumes ``Invoice`` records after they are saved. Mentions discounts and
renewals constantly but never computes them — another place a grep-driven
agent will open and read in full.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict
from typing import Iterable

from shop.billing.invoice_repository import Invoice

EXPORT_COLUMNS = ["id", "subscription_id", "kind", "total_cents", "discount_cents", "discount_pct"]


def discount_pct(invoice: Invoice) -> float:
    """Discount share of the pre-discount total, for finance dashboards."""
    gross = invoice.total_cents + invoice.discount_cents
    return round(invoice.discount_cents / gross * 100, 2) if gross else 0.0


def to_rows(invoices: Iterable[Invoice]) -> list[dict]:
    rows = []
    for inv in invoices:
        d = asdict(inv)
        d["discount_pct"] = discount_pct(inv)
        rows.append({k: d.get(k) for k in EXPORT_COLUMNS})
    return rows


def export_csv(invoices: Iterable[Invoice]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS)
    writer.writeheader()
    for row in to_rows(invoices):
        writer.writerow(row)
    return buf.getvalue()


def export_json(invoices: Iterable[Invoice]) -> str:
    return json.dumps(to_rows(invoices), indent=2)


def renewal_discount_summary(invoices: Iterable[Invoice]) -> dict:
    """Aggregate renewal invoices: count, total discount, mean discount pct.

    Finance uses this to sanity-check that loyalty renewal discounts are being
    applied. It reads the stored ``discount_cents`` and cannot explain *why* a
    discount was zero — that logic lives in the billing discount policy.
    """
    renewals = [i for i in invoices if i.kind == "renewal"]
    total_discount = sum(i.discount_cents for i in renewals)
    zero_discount = [i for i in renewals if i.discount_cents == 0]
    return {
        "renewal_invoices": len(renewals),
        "total_discount_cents": total_discount,
        "zero_discount_renewals": len(zero_discount),
        "mean_discount_pct": round(sum(discount_pct(i) for i in renewals) / len(renewals), 2) if renewals else 0.0,
    }
