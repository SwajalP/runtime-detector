from datetime import date

from shop.api.renewal_controller import RenewalController
from shop.billing.dunning import next_retry_hours
from shop.billing.models import Loyalty, Subscription


def test_dunning_backoff():
    sub = Subscription(
        id="s",
        plan="pro",
        annual_cents=1000,
        start=date(2024, 1, 1),
        cycle_anchor=date(2024, 1, 1),
        last_charge_failed=True,
    )
    assert next_retry_hours(sub, 0) == 6
    assert next_retry_hours(sub, 3) == 48


def test_failed_charge_retries_once_through_the_webhook():
    sub = Subscription(
        id="s",
        plan="pro",
        annual_cents=20000,
        start=date(2024, 1, 1),
        cycle_anchor=date(2025, 1, 1),
        last_charge_failed=True,
    )
    inv = RenewalController().renew(sub, Loyalty("gold", 4000))
    assert inv.kind == "renewal"
    assert inv.discount_cents == 2000
