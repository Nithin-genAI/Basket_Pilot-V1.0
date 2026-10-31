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
    delivery: float | None = None
    handling: float | None = None
    small_cart: float | None = None
    late_night: float | None = None
    other: float | None = None
    free_delivery_threshold: float | None = None
    min_order: float | None = None
    small_cart_threshold: float | None = None
    fees_estimated: bool = False


@dataclass(frozen=True, slots=True)
class Assignment:
    item_id: str
    platform: str
    sku_id: str
    title: str
    qty: int
    # Optimizer output monetary fields are integer paise.
    unit_price: int
    line_total: int
    overbuy_qty: float = 0
    overbuy_unit: str | None = None


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
    # All monetary output fields below are integer paise; no float totals.
    per_platform: dict[str, dict[str, int]] = field(default_factory=dict)
    grand_total: int = 0
    baselines: dict[str, int | None] = field(default_factory=dict)
    savings_vs_best_single: int = 0
    unfulfilled: tuple[str, ...] = ()
    rationale: tuple[str, ...] = ()
    savings_vs_naive: int = 0
    fees_estimated: bool = False
    suggestions: tuple[str, ...] = ()
    objective_paise: int = 0

