from datetime import date

from shop.billing.models import Subscription
from shop.billing.proration import proration_credit


def test_proration_mid_cycle():
    sub = Subscription(
        id="s",
        plan="pro",
        annual_cents=36500,
        start=date(2024, 1, 1),
        cycle_anchor=date(2024, 1, 1),
        mid_cycle_change=True,
    )
    assert proration_credit(sub, unused_days=10).cents == 1000
