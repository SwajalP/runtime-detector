from __future__ import annotations

from shop.billing.models import Money


class TaxAdapter:
    rate = 0.0  # demo locale is tax-free so discount math stays visible

    def for_amount(self, amount: Money) -> Money:
        return Money(int(amount.cents * self.rate))
