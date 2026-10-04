from datetime import date

from shop.api.webhooks import RenewalWebhook
from shop.billing.models import Loyalty


def test_webhook_decodes_provider_payload():
    payload = {
        "subscription_id": "sub_w",
        "plan": "pro",
        "annual_cents": 10000,
        "start": date(2024, 3, 1),
        "cycle_anchor": date(2025, 3, 1),
        "annual_boundary": False,
        "tier": "silver",
        "points": 10,
    }
    inv = RenewalWebhook().handle(payload)
    assert inv.kind == "renewal"
    assert inv.discount_cents == 500
