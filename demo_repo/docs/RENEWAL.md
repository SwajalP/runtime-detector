# Renewal discounts

Loyalty members receive a renewal discount on annual invoices. The discount is a
percentage of the list price determined by loyalty tier:

| Tier | Renewal discount |
|---|---|
| none | 0% |
| silver | 5% |
| gold | 10% |
| platinum | 15% |

## Annual boundary

When a subscription's cycle anchor rolls past its start-date anniversary the
renewal is said to *cross the annual boundary*. Loyalty discounts are intended
to apply on boundary renewals exactly as on mid-year renewals. Customer-facing
copy about the boundary lives in `shop/notifications/renewal_emails.py`.

## Where discounts are described (but not computed)

- `shop/catalog/noise.py` — pricing page copy and SKU discount table (marketing only)
- `shop/promotions/seasonal.py` — checkout coupons marketed as "renewal discounts"
- `shop/legacy/renewal_discount_v1.py` — retired v1 engine kept for archived invoices
- `shop/reporting/invoice_export.py` — finance exports reading `discount_cents`
- `shop/notifications/renewal_emails.py` — email templates formatting the discount

This document is intentionally unhelpful about *where the discount is computed*:
it never names the billing policy function. Agents that stop at documentation
or at the first few grep hits will miss the executed path.

## Related

- Proration on mid-cycle plan changes: `shop/billing/proration.py`
- Dunning retries after failed renewal charges: `shop/billing/dunning.py`
- Provider webhooks that trigger renewals: `shop/api/webhooks.py`
