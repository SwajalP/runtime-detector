from datetime import date

from shop.billing.invoice_repository import Invoice
from shop.billing.models import Loyalty, Subscription
from shop.notifications.renewal_emails import annual_boundary_notice, renewal_receipt, renewal_reminder


def _sub(boundary=False):
    return Subscription("sub_n", "pro", 20000, date(2024, 1, 1), date(2025, 1, 1), crosses_annual_boundary=boundary)


def test_reminder_mentions_estimate():
    email = renewal_reminder(_sub(True), Loyalty("gold", 100), 2000)
    assert "$20.00" in email.body and "annual boundary" in email.body


def test_receipt_formats_discount():
    email = renewal_receipt(_sub(), Invoice("sub_n", 18000, 2000, "renewal", id="inv_9"))
    assert "-$20.00" in email.body


def test_boundary_notice_only_when_crossing():
    assert annual_boundary_notice(_sub(False)) is None
    assert annual_boundary_notice(_sub(True)) is not None
