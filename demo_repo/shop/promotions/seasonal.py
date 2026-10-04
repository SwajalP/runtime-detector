"""Seasonal promotions — coupon codes for the checkout funnel.

Lexical trap: marketing calls these "renewal discount" campaigns, but they are
checkout coupons and never touch ``shop.billing``. Loyalty is mentioned in copy
only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from shop.billing.models import Money


@dataclass(frozen=True)
class Campaign:
    code: str
    label: str
    amount_cents: int
    starts: date
    ends: date
    copy: str


CAMPAIGNS: list[Campaign] = [
    Campaign("SPRING25", "Spring renewal discount", 2500, date(2026, 3, 1), date(2026, 4, 30),
             "Spring renewal discount: $25 off your next checkout. Loyalty members stack this with tier savings."),
    Campaign("SUMMER15", "Summer renewal discount", 1500, date(2026, 6, 1), date(2026, 8, 31),
             "Summer renewal discount coupon for returning customers."),
    Campaign("BLACKFRI", "Black Friday", 5000, date(2026, 11, 27), date(2026, 11, 30),
             "Biggest invoice discount of the year. Not combinable with loyalty renewal discounts."),
    Campaign("LOYAL10", "Loyalty thank-you", 1000, date(2026, 1, 1), date(2026, 12, 31),
             "Thank-you coupon for gold and platinum loyalty members at renewal."),
    Campaign("WINBACK", "Win-back", 3000, date(2026, 1, 1), date(2026, 12, 31),
             "Win-back renewal discount for lapsed subscriptions."),
]


def seasonal_renewal_discount(code: str, today: date | None = None) -> Money:
    """Checkout coupon amount for a campaign code. Unrelated to billing's loyalty policy."""
    today = today or date.today()
    for c in CAMPAIGNS:
        if c.code == code and c.starts <= today <= c.ends:
            return Money(c.amount_cents)
    return Money(0)


def active_campaigns(today: date | None = None) -> list[Campaign]:
    today = today or date.today()
    return [c for c in CAMPAIGNS if c.starts <= today <= c.ends]


def promotion_engine_hint() -> str:
    return "Apply a renewal discount coupon at checkout. Unrelated to loyalty billing policy."


def render_banner(today: date | None = None) -> str:
    """Marketing banner text listing active renewal discount campaigns."""
    lines = []
    for c in active_campaigns(today):
        lines.append(f"{c.label} ({c.code}): {c.copy}")
    return "\n".join(lines) or "No renewal discount campaigns are active."


def stackable_with_loyalty(code: str) -> bool:
    """Whether a coupon may be combined with a loyalty renewal discount on the invoice."""
    return code in {"SPRING25", "LOYAL10"}
