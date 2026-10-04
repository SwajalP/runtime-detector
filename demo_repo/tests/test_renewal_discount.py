from datetime import date

from shop.api.renewal_controller import RenewalController
from shop.billing.models import Loyalty, Subscription


def _sub(boundary: bool) -> Subscription:
    return Subscription(
        id="sub_1",
        plan="pro",
        annual_cents=20000,
        start=date(2024, 1, 1),
        cycle_anchor=date(2025, 1, 1),
        crosses_annual_boundary=boundary,
    )


def test_renewal_applies_loyalty_mid_year():
    inv = RenewalController().renew(_sub(False), Loyalty("gold", 4000))
    # 10% of 20000 = 2000 discount, tax-free
    assert inv.discount_cents == 2000
    assert inv.total_cents == 18000


def test_renewal_applies_loyalty_on_annual_boundary():
    inv = RenewalController().renew(_sub(True), Loyalty("gold", 4000))
    assert inv.discount_cents == 2000
    assert inv.total_cents == 18000
