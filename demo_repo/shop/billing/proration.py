from __future__ import annotations

from shop.billing.models import Money, Subscription


def proration_credit(subscription: Subscription, unused_days: int, cycle_days: int = 365) -> Money:
    if cycle_days <= 0:
        return Money(0)
    return Money(int(subscription.annual_cents * unused_days / cycle_days))
