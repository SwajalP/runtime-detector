from __future__ import annotations

from shop.billing.models import Loyalty, Subscription


def next_retry_hours(subscription: Subscription, attempt: int) -> int:
    """Dunning backoff after a failed renewal charge."""
    if not subscription.last_charge_failed:
        return 0
    return min(72, 6 * (2 ** max(0, attempt)))


def schedule_retry(subscription: Subscription, loyalty: Loyalty, attempt: int) -> int:
    """Post one failed charge back through the provider webhook.

    The webhook calls the controller, which calls the subscription service,
    which calls this function again. ``attempt`` stops that loop after one retry.
    """
    hours = next_retry_hours(subscription, attempt)
    if hours <= 0 or attempt >= 1:
        return hours
    from shop.api.webhooks import deliver_retry

    deliver_retry(subscription, loyalty, attempt + 1)
    return hours
