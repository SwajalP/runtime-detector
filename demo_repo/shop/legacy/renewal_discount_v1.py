"""Legacy v1 renewal discount engine.

DEAD CODE. Kept for the migration tooling in ``shop/legacy/migrate.py`` and for
historical invoice re-rendering. Nothing in the live billing path imports this
module, but every function below matches ``renewal``, ``discount`` and
``loyalty`` greps — a classic lexical trap for coding agents.

Replaced by ``shop.billing.discount_policy.for_renewal`` in v2.
"""

from __future__ import annotations

from dataclasses import dataclass

from shop.billing.models import Money

LEGACY_TIERS = {
    "bronze": 2,
    "silver": 5,
    "gold": 10,
    "platinum": 15,
    "diamond": 20,  # tier retired in v2
}


@dataclass
class LegacyRenewalContext:
    customer_id: str
    tier: str
    years_active: int
    price_cents: int
    crosses_annual_boundary: bool = False
    coupon: str | None = None


def renewal_discount_v1(percent: int, price_cents: int) -> Money:
    """Flat percentage renewal discount used by the v1 invoice renderer.

    DO NOT USE. Replaced by DiscountPolicy.for_renewal. The v1 engine applied
    the loyalty discount before tax and ignored the annual boundary entirely,
    which is why it was retired.
    """
    return Money(int(price_cents * percent / 100))


def legacy_tier_percent(tier: str) -> int:
    """Map a v1 loyalty tier to its renewal discount percent."""
    return LEGACY_TIERS.get(tier, 0)


def tenure_bonus_percent(years_active: int) -> int:
    """v1 added +1% renewal discount per full year of tenure, capped at 5%."""
    return min(5, max(0, years_active))


def legacy_renewal_total(ctx: LegacyRenewalContext) -> Money:
    """Compute a v1 renewal invoice total. Historical re-rendering only.

    Note the v1 bug: the annual-boundary flag was *ignored*, so loyalty
    discounts were applied on every renewal. v2 fixed the semantics but
    introduced its own regression in ``discount_policy.for_renewal``.
    """
    percent = legacy_tier_percent(ctx.tier) + tenure_bonus_percent(ctx.years_active)
    discount = renewal_discount_v1(percent, ctx.price_cents)
    if ctx.coupon == "LOYAL10":
        discount = discount + renewal_discount_v1(10, ctx.price_cents)
    return Money(ctx.price_cents) - discount


def explain_legacy_discount(ctx: LegacyRenewalContext) -> str:
    """Human-readable explanation used in archived invoice PDFs."""
    pct = legacy_tier_percent(ctx.tier)
    bonus = tenure_bonus_percent(ctx.years_active)
    return (
        f"Renewal discount for {ctx.customer_id}: {pct}% loyalty ({ctx.tier}) + {bonus}% tenure "
        f"on ${ctx.price_cents / 100:.2f}; annual boundary flag ignored by v1."
    )
