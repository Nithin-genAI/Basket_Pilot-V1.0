"""uAgents wire contracts, separate from the platform-neutral domain dataclasses."""

from __future__ import annotations

from uagents import Model


class ItemData(Model):
    id: str
    name: str
    qty: float = 1
    unit: str | None = None
    brand: str | None = None
    notes: str | None = None


class CandidateData(Model):
    sku_id: str
    title: str
    brand: str | None = None
    pack_size: str = ""
    price: float
    mrp: float | None = None
    unit_price: float | None = None
    in_stock: bool = True
    max_qty: int | None = None
    eta_min: int | None = None
    match_score: float = 1


class ItemCandidatesData(Model):
    item_id: str
    candidates: list[CandidateData]


class FeeScheduleData(Model):
    delivery: float | None = None
    handling: float | None = None
    small_cart: float | None = None
    late_night: float | None = None
    other: float | None = None
    free_delivery_threshold: float | None = None
    min_order: float | None = None
    small_cart_threshold: float | None = None
    fees_estimated: bool = False


class AssignmentData(Model):
    item_id: str
    platform: str
    sku_id: str
    title: str
    qty: int
    unit_price: int
    line_total: int
    overbuy_qty: float = 0
    overbuy_unit: str | None = None


class ParseRequest(Model):
    session_id: str
    text: str


class ParsedList(Model):
    session_id: str
    items: list[ItemData]
    ambiguities: list[str] = []


class SearchRequest(Model):
    session_id: str
    items: list[ItemData]
    address_id: str | None = None


class SearchResult(Model):
    session_id: str
    platform: str
    results: list[ItemCandidatesData]
    store_status: str = "OK"
    notice: str | None = None
    fee_schedule: FeeScheduleData | None = None


class OptimizeRequest(Model):
    session_id: str
    items: list[ItemData]
    results: list[SearchResult]
    multi_platform_penalty_paise: int = 1000


class CartPlan(Model):
    session_id: str
    assignments: list[AssignmentData]
    per_platform: dict[str, dict[str, int]]
    grand_total: int
    baselines: dict[str, int | None]
    savings_vs_best_single: int
    savings_vs_naive: int
    unfulfilled: list[str]
    rationale: list[str]
    fees_estimated: bool = False
    suggestions: list[str] = []
    objective_paise: int = 0


class CheckoutRequest(Model):
    session_id: str
    action: str
    platform: str
    assignments: list[AssignmentData]
    address_id: str | None = None


class PreviewLineData(Model):
    sku_id: str
    title: str
    qty: int
    unit_price_paise: int
    line_total_paise: int


class CheckoutPreview(Model):
    session_id: str
    platform: str
    lines: list[PreviewLineData]
    fee_lines: dict[str, int]
    subtotal_paise: int
    total_paise: int
    preview_token: str
    expires_at: float
    fees_estimated: bool = False
    validation_errors: list[str] = []


class ConfirmRequest(Model):
    session_id: str
    platform: str
    preview_token: str


class OrderResult(Model):
    session_id: str
    platform: str
    status: str
    order_ref: str | None = None
    payment_qr_path: str | None = None
    error: str | None = None
