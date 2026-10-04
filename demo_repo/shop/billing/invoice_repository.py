from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class Invoice:
    subscription_id: str
    total_cents: int
    discount_cents: int
    kind: str
    id: str | None = None


class InvoiceRepository:
    def __init__(self) -> None:
        self._items: Dict[str, Invoice] = {}
        self._n = 0

    def save(self, invoice: Invoice) -> Invoice:
        self._n += 1
        invoice.id = f"inv_{self._n}"
        self._items[invoice.id] = invoice
        return invoice

    def get(self, invoice_id: str) -> Invoice | None:
        return self._items.get(invoice_id)
