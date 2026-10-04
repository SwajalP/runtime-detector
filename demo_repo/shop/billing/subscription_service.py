from __future__ import annotations

from shop.billing.discount_policy import for_renewal
from shop.billing.invoice_repository import Invoice, InvoiceRepository
from shop.billing.models import Loyalty, Money, Subscription
from shop.billing.tax_adapter import TaxAdapter


class SubscriptionService:
    def __init__(self, invoices: InvoiceRepository | None = None, tax: TaxAdapter | None = None):
        self.invoices = invoices or InvoiceRepository()
        self.tax = tax or TaxAdapter()

    def calculate_total(self, subscription: Subscription, loyalty: Loyalty) -> Money:
        discount = for_renewal(subscription, loyalty)
        subtotal = Money(subscription.annual_cents) - discount
        tax = self.tax.for_amount(subtotal)
        return subtotal + tax

    def renew(self, subscription: Subscription, loyalty: Loyalty, *, attempt: int = 0) -> Invoice:
        total = self.calculate_total(subscription, loyalty)
        invoice = Invoice(
            subscription_id=subscription.id,
            total_cents=total.cents,
            discount_cents=for_renewal(subscription, loyalty).cents,
            kind="renewal",
        )
        saved = self.invoices.save(invoice)
        if subscription.last_charge_failed and attempt < 1:
            from shop.billing.dunning import schedule_retry

            schedule_retry(subscription, loyalty, attempt)
        return saved
