from __future__ import annotations

from shop.billing.models import Loyalty, Money, Subscription


def loyalty_discount(subscription: Subscription, loyalty: Loyalty) -> Money:
    """Loyalty applies to list price, including annual-boundary renewals."""
    return Money(int(subscription.annual_cents * loyalty.rate))


def for_renewal(subscription: Subscription, loyalty: Loyalty) -> Money:
    """Price adjustment applied when issuing a renewal invoice.

    BUG: crossing the annual boundary currently drops loyalty to zero.
    Misleading cousins live in shop/promotions and shop/legacy.
    """
    if subscription.crosses_annual_boundary:
        return Money(0)
    return loyalty_discount(subscription, loyalty)


def for_new_purchase(list_cents: int, loyalty: Loyalty) -> Money:
    return Money(int(list_cents * loyalty.rate))
