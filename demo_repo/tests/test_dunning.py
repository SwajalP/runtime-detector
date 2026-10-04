from datetime import date

from shop.billing.dunning import next_retry_hours
from shop.billing.models import Subscription


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
