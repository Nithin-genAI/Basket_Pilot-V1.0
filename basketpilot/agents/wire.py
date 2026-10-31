"""Conversions between immutable domain dataclasses and uAgents wire Models."""

from __future__ import annotations

from basketpilot import messages as wire
from basketpilot.models import (
    Assignment, Candidate, CartPlan as DomainCartPlan, FeeSchedule,
    ItemCandidates, ParsedItem, SearchResult as DomainSearchResult, StoreStatus,
)


def item_to_wire(item: ParsedItem) -> wire.ItemData:
    return wire.ItemData(id=item.id, name=item.name, qty=item.qty, unit=item.unit, brand=item.brand, notes=item.notes)


def item_from_wire(item: wire.ItemData) -> ParsedItem:
    return ParsedItem(id=item.id, name=item.name, qty=item.qty, unit=item.unit, brand=item.brand, notes=item.notes)


def schedule_to_wire(schedule: FeeSchedule | None) -> wire.FeeScheduleData | None:
    if schedule is None:
        return None
    return wire.FeeScheduleData(**{
        field: getattr(schedule, field)
        for field in (
            "delivery", "handling", "small_cart", "late_night", "other",
            "free_delivery_threshold", "min_order", "small_cart_threshold", "fees_estimated",
        )
    })


def schedule_from_wire(schedule: wire.FeeScheduleData | None) -> FeeSchedule | None:
    if schedule is None:
        return None
    return FeeSchedule(**{
        field: getattr(schedule, field)
        for field in (
            "delivery", "handling", "small_cart", "late_night", "other",
            "free_delivery_threshold", "min_order", "small_cart_threshold", "fees_estimated",
        )
    })


def result_to_wire(session_id: str, platform: str, results: tuple[ItemCandidates, ...], status: StoreStatus, notice: str | None, schedule: FeeSchedule | None) -> wire.SearchResult:
    entries = [wire.ItemCandidatesData(item_id=row.item_id, candidates=[
        wire.CandidateData(**{
            key: getattr(candidate, key)
            for key in ("sku_id", "title", "brand", "pack_size", "price", "mrp", "unit_price", "in_stock", "max_qty", "eta_min", "match_score")
        }) for candidate in row.candidates
    ]) for row in results]
    return wire.SearchResult(session_id=session_id, platform=platform, results=entries, store_status=str(status), notice=notice, fee_schedule=schedule_to_wire(schedule))


def result_from_wire(result: wire.SearchResult) -> DomainSearchResult:
    rows = tuple(ItemCandidates(item_id=row.item_id, candidates=tuple(
        Candidate(**{key: getattr(candidate, key) for key in (
            "sku_id", "title", "brand", "pack_size", "price", "mrp", "unit_price", "in_stock", "max_qty", "eta_min", "match_score"
        )}) for candidate in row.candidates
    )) for row in result.results)
    try:
        status = StoreStatus(result.store_status)
    except ValueError:
        status = StoreStatus.UNAVAILABLE
    return DomainSearchResult(platform=result.platform, results=rows, store_status=status, notice=result.notice)


def plan_to_wire(session_id: str, plan: DomainCartPlan) -> wire.CartPlan:
    return wire.CartPlan(
        session_id=session_id,
        assignments=[wire.AssignmentData(**{
            field: getattr(row, field)
            for field in ("item_id", "platform", "sku_id", "title", "qty", "unit_price", "line_total", "overbuy_qty", "overbuy_unit")
        }) for row in plan.assignments],
        per_platform=plan.per_platform, grand_total=plan.grand_total,
        baselines=plan.baselines, savings_vs_best_single=plan.savings_vs_best_single,
        savings_vs_naive=plan.savings_vs_naive, unfulfilled=list(plan.unfulfilled),
        rationale=list(plan.rationale), fees_estimated=plan.fees_estimated,
        suggestions=list(plan.suggestions), objective_paise=plan.objective_paise,
    )


def model_data(value: object) -> dict:
    """uAgents Model uses Pydantic v1 compatibility; keep one boundary helper."""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()  # type: ignore[attr-defined,no-any-return]


def assignment_from_wire(row: wire.AssignmentData) -> Assignment:
    return Assignment(**{
        field: getattr(row, field)
        for field in ("item_id", "platform", "sku_id", "title", "qty", "unit_price", "line_total", "overbuy_qty", "overbuy_unit")
    })


def plan_from_wire(plan: wire.CartPlan) -> DomainCartPlan:
    return DomainCartPlan(
        assignments=tuple(assignment_from_wire(row) for row in plan.assignments),
        per_platform=plan.per_platform, grand_total=plan.grand_total, baselines=plan.baselines,
        savings_vs_best_single=plan.savings_vs_best_single, savings_vs_naive=plan.savings_vs_naive,
        unfulfilled=tuple(plan.unfulfilled), rationale=tuple(plan.rationale), fees_estimated=plan.fees_estimated,
        suggestions=tuple(plan.suggestions), objective_paise=plan.objective_paise,
    )
