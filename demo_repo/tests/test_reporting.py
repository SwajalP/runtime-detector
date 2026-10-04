from shop.billing.invoice_repository import Invoice
from shop.reporting.invoice_export import export_csv, renewal_discount_summary


def test_export_and_summary():
    invoices = [
        Invoice("s1", 18000, 2000, "renewal", id="inv_1"),
        Invoice("s2", 20000, 0, "renewal", id="inv_2"),
        Invoice("s3", 900, 0, "new", id="inv_3"),
    ]
    csv_text = export_csv(invoices)
    assert "inv_1" in csv_text and "discount_pct" in csv_text
    summary = renewal_discount_summary(invoices)
    assert summary["renewal_invoices"] == 2
    assert summary["zero_discount_renewals"] == 1
