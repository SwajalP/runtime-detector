"""Renewal email templates.

Templates reference the loyalty renewal discount, the annual boundary and the
invoice total, but they only *format* values handed to them. They never decide
the discount. Agents grepping for "annual" or "loyalty discount" land here.
"""

from __future__ import annotations

from dataclasses import dataclass

from shop.billing.invoice_repository import Invoice
from shop.billing.models import Loyalty, Subscription


@dataclass(frozen=True)
class Email:
    subject: str
    body: str


def _dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def renewal_reminder(sub: Subscription, loyalty: Loyalty, estimated_discount_cents: int) -> Email:
    """Sent 14 days before renewal with an *estimated* loyalty discount."""
    boundary = " This renewal crosses your annual boundary." if sub.crosses_annual_boundary else ""
    return Email(
        subject=f"Your {sub.plan} plan renews soon",
        body=(
            f"Hi! Your subscription {sub.id} renews on {sub.cycle_anchor:%B %d}.{boundary}\n"
            f"As a {loyalty.tier} loyalty member you are estimated to save {_dollars(estimated_discount_cents)} "
            f"on a {_dollars(sub.annual_cents)} list price. The final loyalty discount is computed on the invoice."
        ),
    )


def renewal_receipt(sub: Subscription, invoice: Invoice) -> Email:
    """Receipt after a renewal invoice is saved. Formats discount_cents; does not compute it."""
    discount_line = (
        f"Loyalty renewal discount: -{_dollars(invoice.discount_cents)}\n" if invoice.discount_cents else
        "Loyalty renewal discount: not applied\n"
    )
    return Email(
        subject=f"Receipt for invoice {invoice.id}",
        body=(
            f"Thanks for renewing {sub.plan}.\n"
            f"List price: {_dollars(sub.annual_cents)}\n"
            f"{discount_line}"
            f"Total charged: {_dollars(invoice.total_cents)}"
        ),
    )


def annual_boundary_notice(sub: Subscription) -> Email | None:
    """Explain the annual boundary to customers whose cycle anchor rolls over."""
    if not sub.crosses_annual_boundary:
        return None
    return Email(
        subject="Your plan year is rolling over",
        body=(
            "Your subscription crosses its annual renewal boundary this cycle. Loyalty tier and renewal "
            "discounts carry over; see your invoice for the applied amount."
        ),
    )


def dunning_warning(sub: Subscription, attempt: int, retry_hours: int) -> Email:
    return Email(
        subject="Payment failed — we will retry",
        body=f"Renewal charge for {sub.id} failed (attempt {attempt}). We will retry in {retry_hours} hours.",
    )
