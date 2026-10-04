from __future__ import annotations

from shop.billing.models import Loyalty, Subscription
from shop.billing.subscription_service import SubscriptionService


class RenewalController:
    def __init__(self, service: SubscriptionService | None = None):
        self.service = service or SubscriptionService()

    def renew(self, subscription: Subscription, loyalty: Loyalty, *, attempt: int = 0):
        return self.service.renew(subscription, loyalty, attempt=attempt)
