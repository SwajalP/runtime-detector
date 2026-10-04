from __future__ import annotations

from shop.billing.models import Subscription


def next_retry_hours(subscription: Subscription, attempt: int) -> int:
    """Dunning backoff after a failed renewal charge."""
    if not subscription.last_charge_failed:
        return 0
    return min(72, 6 * (2 ** max(0, attempt)))
