from __future__ import annotations

from shop.api.renewal_controller import RenewalController
from shop.billing.models import Loyalty, Subscription


def deliver_retry(subscription: Subscription, loyalty: Loyalty, attempt: int):
    """Dunning posts a failed charge back through the provider webhook."""
    payload = {
        "subscription_id": subscription.id,
        "plan": subscription.plan,
        "annual_cents": subscription.annual_cents,
        "start": subscription.start,
        "cycle_anchor": subscription.cycle_anchor,
        "annual_boundary": subscription.crosses_annual_boundary,
        "tier": loyalty.tier,
        "points": loyalty.points,
        "last_charge_failed": True,
        "retry_attempt": attempt,
    }
    return RenewalWebhook().handle(payload)


class RenewalWebhook:
    def __init__(self, controller: RenewalController | None = None):
        self.controller = controller or RenewalController()

    def handle(self, payload: dict):
        sub = Subscription(
            id=payload["subscription_id"],
            plan=payload.get("plan", "pro"),
            annual_cents=int(payload.get("annual_cents", 12000)),
            start=payload["start"],
            cycle_anchor=payload["cycle_anchor"],
            crosses_annual_boundary=bool(payload.get("annual_boundary")),
        )
        loyalty = Loyalty(tier=payload.get("tier", "none"), points=int(payload.get("points", 0)))
        if payload.get("last_charge_failed"):
            sub.last_charge_failed = True
        attempt = int(payload.get("retry_attempt") or 0)
        return self.controller.renew(sub, loyalty, attempt=attempt)
