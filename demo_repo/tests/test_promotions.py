from datetime import date

from shop.promotions.seasonal import seasonal_renewal_discount, stackable_with_loyalty


def test_spring_coupon_window():
    assert seasonal_renewal_discount("SPRING25", date(2026, 3, 15)).cents == 2500
    assert seasonal_renewal_discount("SPRING25", date(2026, 7, 1)).cents == 0


def test_stackable():
    assert stackable_with_loyalty("LOYAL10")
    assert not stackable_with_loyalty("BLACKFRI")
