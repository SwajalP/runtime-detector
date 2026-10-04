from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class Money:
    cents: int

    def __add__(self, other: "Money") -> "Money":
        return Money(self.cents + other.cents)

    def __sub__(self, other: "Money") -> "Money":
        return Money(self.cents - other.cents)

    @classmethod
    def from_dollars(cls, dollars: float) -> "Money":
        return cls(int(round(dollars * 100)))


@dataclass
class Loyalty:
    tier: str
    points: int

    @property
    def rate(self) -> float:
        return {"none": 0.0, "silver": 0.05, "gold": 0.10, "platinum": 0.15}.get(self.tier, 0.0)


@dataclass
class Subscription:
    id: str
    plan: str
    annual_cents: int
    start: date
    cycle_anchor: date
    crosses_annual_boundary: bool = False
    mid_cycle_change: bool = False
    last_charge_failed: bool = False
