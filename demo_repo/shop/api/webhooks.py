from __future__ import annotations

from shop.api.renewal_controller import RenewalController
from shop.billing.models import Loyalty, Subscription


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
        return self.controller.renew(sub, loyalty)
