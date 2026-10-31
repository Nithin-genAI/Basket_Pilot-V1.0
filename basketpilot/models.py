"""Shared, platform-neutral data contracts for BasketPilot."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class StoreStatus(StrEnum):
    OK = "OK"
    CLOSED = "CLOSED"
    UNSERVICEABLE = "UNSERVICEABLE"
    NEEDS_ADDRESS = "NEEDS_ADDRESS"
    NEEDS_LOGIN = "NEEDS_LOGIN"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ParsedItem:
    id: str
    name: str
    qty: float = 1
    unit: str | None = None
    brand: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedList:
    items: tuple[ParsedItem, ...]
    ambiguities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Candidate:
    sku_id: str
    title: str
    brand: str | None
    pack_size: str
    price: float
    mrp: float | None = None
    unit_price: float | None = None
    in_stock: bool = True
    max_qty: int | None = None
    eta_min: int | None = None
    match_score: float = 1.0


@dataclass(frozen=True, slots=True)
class ItemCandidates:
    item_id: str
    candidates: tuple[Candidate, ...]


@dataclass(frozen=True, slots=True)
class SearchResult:
    platform: str
    results: tuple[ItemCandidates, ...]
    store_status: StoreStatus = StoreStatus.OK
    notice: str | None = None


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    delivery: float = 0
    handling: float = 0
    small_cart: float = 0
    late_night: float = 0
    other: float = 0
    free_delivery_threshold: float | None = None
    min_order: float | None = None


@dataclass(frozen=True, slots=True)
class Assignment:
    item_id: str
    platform: str
    sku_id: str
    title: str
    qty: int
    unit_price: float
    line_total: float


@dataclass(frozen=True, slots=True)
class CartLine:
    sku_id: str
    title: str
    qty: int
    unit_price: float


@dataclass(frozen=True, slots=True)
class CartSnapshot:
    platform: str
    lines: tuple[CartLine, ...]
    subtotal: float
    fees: FeeSchedule
    total: float
    validation_errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CartPlan:
    assignments: tuple[Assignment, ...]
    per_platform: dict[str, dict[str, float]] = field(default_factory=dict)
    grand_total: float = 0
    baselines: dict[str, float] = field(default_factory=dict)
    savings_vs_best_single: float = 0
    unfulfilled: tuple[str, ...] = ()
    rationale: tuple[str, ...] = ()

