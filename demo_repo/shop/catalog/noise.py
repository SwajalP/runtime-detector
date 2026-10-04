"""Catalog copy and SKU tables.

This module is a *lexical trap*: it mentions renewal, discount, loyalty and
invoice dozens of times in marketing copy and never participates in billing.
A naive grep-then-read agent pays for every line below.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogEntry:
    sku: str
    title: str
    blurb: str
    list_cents: int


DISCOUNT_COPY = [
    "Renewal discount FAQ: loyalty members see their renewal discount on the invoice preview.",
    "Loyalty discount tiers (silver, gold, platinum) are described in the pricing page, not here.",
    "Invoice discount codes for partners are issued quarterly by the partnerships team.",
    "Seasonal renewal discount campaigns are configured in the promotions module.",
    "Annual renewal reminders include a loyalty discount estimate for marketing purposes only.",
    "The renewal discount shown in email previews is illustrative and not the billed amount.",
    "Discounts on invoices are rounded to the nearest cent by the billing service.",
    "Loyalty points never expire while a subscription renews on time.",
]


CATALOG: list[CatalogEntry] = [
    CatalogEntry("sku_pro_annual", "Pro (annual)", "Best value with the loyalty renewal discount applied at invoice time.", 20000),
    CatalogEntry("sku_pro_monthly", "Pro (monthly)", "Flexible plan; renewal discount not available on monthly invoices.", 2000),
    CatalogEntry("sku_team_annual", "Team (annual)", "Includes seat-based invoice discounts for growing teams.", 60000),
    CatalogEntry("sku_team_monthly", "Team (monthly)", "Monthly team plan; loyalty tiers accrue points toward renewal discounts.", 6000),
    CatalogEntry("sku_enterprise", "Enterprise", "Custom invoice terms; renewal discounts negotiated per contract.", 250000),
    CatalogEntry("sku_addon_support", "Priority support", "Add-on billed on the same renewal invoice as the base plan.", 5000),
    CatalogEntry("sku_addon_sso", "SSO", "Security add-on; eligible for loyalty discount at renewal.", 8000),
    CatalogEntry("sku_addon_audit", "Audit logs", "Compliance add-on; appears as a separate line on the invoice.", 7000),
    CatalogEntry("sku_addon_regions", "Data regions", "Regional hosting; proration applies on mid-cycle changes.", 12000),
    CatalogEntry("sku_addon_sla", "99.99% SLA", "Uptime SLA; renewal discount applies only to annual contracts.", 15000),
    CatalogEntry("sku_starter", "Starter", "Entry plan; no renewal discount, no loyalty accrual.", 900),
    CatalogEntry("sku_student", "Student", "Verified students; invoice discount of 50% applied at checkout.", 450),
    CatalogEntry("sku_nonprofit", "Nonprofit", "Verified nonprofits; renewal discount matches gold loyalty tier.", 10000),
    CatalogEntry("sku_reseller", "Reseller bundle", "Partner invoice discounts applied via partner codes.", 180000),
    CatalogEntry("sku_sandbox", "Sandbox", "Free developer plan; never renews, never invoices.", 0),
    CatalogEntry("sku_legacy_v1", "Legacy v1", "Grandfathered renewal discount from the v1 policy (see shop/legacy).", 15000),
    CatalogEntry("sku_legacy_v2", "Legacy v2", "Grandfathered loyalty discount schedule; migrates on next renewal.", 17000),
    CatalogEntry("sku_trial", "Trial", "14-day trial; converts to Pro with the standard renewal invoice.", 0),
    CatalogEntry("sku_gift_annual", "Gift (annual)", "Gift subscriptions carry no loyalty discount at renewal.", 20000),
    CatalogEntry("sku_marketplace", "Marketplace listing", "Listing fee invoiced monthly; discounts via promo codes.", 3000),
]


SKU_DISCOUNT_TABLE: dict[str, int] = {
    "sku_pro_annual": 0, "sku_pro_monthly": 0, "sku_team_annual": 500, "sku_team_monthly": 0,
    "sku_enterprise": 0, "sku_addon_support": 0, "sku_addon_sso": 0, "sku_addon_audit": 0,
    "sku_addon_regions": 0, "sku_addon_sla": 0, "sku_starter": 0, "sku_student": 50,
    "sku_nonprofit": 10, "sku_reseller": 15, "sku_sandbox": 0, "sku_legacy_v1": 5,
    "sku_legacy_v2": 7, "sku_trial": 0, "sku_gift_annual": 0, "sku_marketplace": 0,
}


def catalog_entries() -> list[CatalogEntry]:
    """Return every catalog entry. Marketing only; billing never calls this."""
    return list(CATALOG)


def discount_copy_for(sku: str) -> list[str]:
    """Marketing copy lines mentioning renewal/loyalty/invoice discounts for a SKU."""
    entry = next((e for e in CATALOG if e.sku == sku), None)
    if entry is None:
        return []
    return [entry.blurb, *DISCOUNT_COPY]


def unused_discount_table() -> dict[str, int]:
    """Percent discounts shown on the pricing page. NOT used by invoicing."""
    return dict(SKU_DISCOUNT_TABLE)


def render_pricing_page() -> str:
    lines = ["# Pricing", ""]
    for e in CATALOG:
        pct = SKU_DISCOUNT_TABLE.get(e.sku, 0)
        suffix = f" ({pct}% invoice discount)" if pct else ""
        lines.append(f"- **{e.title}** — ${e.list_cents / 100:.2f}{suffix}: {e.blurb}")
    lines += ["", *DISCOUNT_COPY]
    return "\n".join(lines)
