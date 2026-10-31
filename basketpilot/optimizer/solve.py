"""Deterministic fee-aware basket assignment using pure functions."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from basketpilot.models import (
    Assignment, CartPlan, FeeSchedule, ParsedItem, SearchResult, StoreStatus,
)
from basketpilot.optimizer.fees import FeeResult, compute_fees
from basketpilot.optimizer.normalize import (
    NormalizedOffer, best_offer, paise_to_rupees, rupees_to_paise,
)


@dataclass(frozen=True, slots=True)
class OptimizerConfig:
    multi_platform_penalty_paise: int = 1000

    @classmethod
    def from_value(cls, config: object | None) -> OptimizerConfig:
        if config is None:
            return cls()
        if isinstance(config, cls):
            return config
        if isinstance(config, dict):
            if "multi_platform_penalty_paise" in config:
                return cls(max(0, int(config["multi_platform_penalty_paise"])))
            if "multi_platform_penalty" in config:
                return cls(max(0, rupees_to_paise(config["multi_platform_penalty"])))
        if hasattr(config, "multi_platform_penalty_paise"):
            return cls(max(0, int(getattr(config, "multi_platform_penalty_paise"))))
        if hasattr(config, "multi_platform_penalty"):
            return cls(max(0, rupees_to_paise(getattr(config, "multi_platform_penalty"))))
        return cls()


@dataclass(frozen=True, slots=True)
class _Evaluation:
    assignments: tuple[tuple[ParsedItem, NormalizedOffer], ...]
    per_platform: dict[str, dict[str, int]]
    grand_total: int
    objective: int
    used_platforms: tuple[str, ...]
    fees_estimated: bool


def _status_is_ok(status: StoreStatus | str) -> bool:
    return str(status) in {StoreStatus.OK.value, "StoreStatus.OK"}


def _available_by_item(
    items: tuple[ParsedItem, ...],
    results: tuple[SearchResult, ...],
) -> dict[str, dict[str, NormalizedOffer]]:
    by_id = {item.id: item for item in items}
    available: dict[str, dict[str, NormalizedOffer]] = {item.id: {} for item in items}
    for result in sorted(results, key=lambda entry: entry.platform):
        if not _status_is_ok(result.store_status):
            continue
        candidates_by_id = {entry.item_id: entry.candidates for entry in result.results}
        for item_id in sorted(by_id):
            offer = best_offer(by_id[item_id], result.platform, candidates_by_id.get(item_id, ()))
            if offer is not None:
                available[item_id][result.platform] = offer
    return available


def _choose_offer(offers: list[NormalizedOffer]) -> NormalizedOffer:
    return min(
        offers,
        key=lambda offer: (
            offer.line_total_paise,
            -offer.candidate.match_score,
            offer.platform,
            offer.candidate.sku_id,
        ),
    )


def _evaluate(
    chosen: dict[str, NormalizedOffer],
    item_by_id: dict[str, ParsedItem],
    fee_schedules: dict[str, FeeSchedule | None],
    penalty_paise: int,
) -> _Evaluation | None:
    platform_subtotals: dict[str, int] = {}
    for offer in chosen.values():
        platform_subtotals[offer.platform] = (
            platform_subtotals.get(offer.platform, 0) + offer.line_total_paise
        )
    used = tuple(sorted(platform_subtotals))
    fee_results: dict[str, FeeResult] = {}
    for platform in used:
        fee_result = compute_fees(
            platform_subtotals[platform], fee_schedules.get(platform)
        )
        if not fee_result.feasible:
            return None
        fee_results[platform] = fee_result

    per_platform: dict[str, dict[str, int]] = {}
    for platform in used:
        line_total = platform_subtotals[platform]
        lines = dict(fee_results[platform].lines_paise)
        lines["subtotal"] = line_total
        lines["platform_total"] = line_total + fee_results[platform].total_paise
        per_platform[platform] = lines
    grand_total = sum(lines["platform_total"] for lines in per_platform.values())
    objective = grand_total + penalty_paise * max(0, len(used) - 1)
    assignment_pairs = tuple(
        (item_by_id[item_id], chosen[item_id]) for item_id in sorted(chosen)
    )
    return _Evaluation(
        assignments=assignment_pairs,
        per_platform=per_platform,
        grand_total=grand_total,
        objective=objective,
        used_platforms=used,
        fees_estimated=any(result.fees_estimated for result in fee_results.values()),
    )


def _repair_thresholds(
    starting: dict[str, NormalizedOffer],
    allowed_platforms: tuple[str, ...],
    available: dict[str, dict[str, NormalizedOffer]],
    item_by_id: dict[str, ParsedItem],
    fee_schedules: dict[str, FeeSchedule | None],
    penalty_paise: int,
) -> tuple[dict[str, NormalizedOffer], tuple[str, ...]]:
    chosen = dict(starting)
    reasons: list[str] = []
    current = _evaluate(chosen, item_by_id, fee_schedules, penalty_paise)
    if current is None:
        return chosen, ()

    # Each accepted move strictly lowers an integer objective; cap work for bounded latency.
    max_moves = max(1, len(chosen) * max(1, len(allowed_platforms)))
    for _ in range(max_moves):
        best_move: tuple[tuple[object, ...], str, NormalizedOffer, _Evaluation] | None = None
        for item_id in sorted(chosen):
            old_offer = chosen[item_id]
            for platform in allowed_platforms:
                new_offer = available.get(item_id, {}).get(platform)
                if new_offer is None or new_offer.platform == old_offer.platform:
                    continue
                candidate_choice = dict(chosen)
                candidate_choice[item_id] = new_offer
                evaluation = _evaluate(candidate_choice, item_by_id, fee_schedules, penalty_paise)
                if evaluation is None or evaluation.objective >= current.objective:
                    continue
                key: tuple[object, ...] = (
                    evaluation.objective,
                    len(evaluation.used_platforms),
                    evaluation.used_platforms,
                    tuple(pair[1].candidate.sku_id for pair in evaluation.assignments),
                    item_id,
                    new_offer.platform,
                    new_offer.candidate.sku_id,
                )
                if best_move is None or key < best_move[0]:
                    best_move = (key, item_id, new_offer, evaluation)
        if best_move is None:
            break
        _, item_id, new_offer, next_eval = best_move
        net_saving = current.objective - next_eval.objective
        threshold_reason = ""
        schedule = fee_schedules.get(new_offer.platform)
        previous_lines = current.per_platform.get(new_offer.platform, {})
        next_lines = next_eval.per_platform.get(new_offer.platform, {})
        if schedule is not None:
            if (
                schedule.free_delivery_threshold is not None
                and previous_lines.get("subtotal", 0) < rupees_to_paise(schedule.free_delivery_threshold)
                and next_lines.get("subtotal", 0) >= rupees_to_paise(schedule.free_delivery_threshold)
            ):
                threshold_reason = " to cross the free-delivery threshold"
            elif (
                schedule.small_cart_threshold is not None
                and previous_lines.get("subtotal", 0) < rupees_to_paise(schedule.small_cart_threshold)
                and next_lines.get("subtotal", 0) >= rupees_to_paise(schedule.small_cart_threshold)
            ):
                threshold_reason = " to clear the small-cart fee"
        reasons.append(
            f"Moved {item_by_id[item_id].name} to {new_offer.platform}{threshold_reason}, "
            f"net saving Rs {paise_to_rupees(net_saving)}."
        )
        chosen[item_id] = new_offer
        current = next_eval
    return chosen, tuple(reasons)


def _evaluation_key(evaluation: _Evaluation) -> tuple[object, ...]:
    return (
        evaluation.objective,
        len(evaluation.used_platforms),
        evaluation.used_platforms,
        tuple(pair[1].candidate.sku_id for pair in evaluation.assignments),
    )


def _subset_evaluations(
    active_platforms: tuple[str, ...],
    item_ids: tuple[str, ...],
    available: dict[str, dict[str, NormalizedOffer]],
    item_by_id: dict[str, ParsedItem],
    fee_schedules: dict[str, FeeSchedule | None],
    penalty_paise: int,
    *,
    require_complete: bool,
) -> list[tuple[_Evaluation, tuple[str, ...]]]:
    evaluations: list[tuple[_Evaluation, tuple[str, ...]]] = []
    for size in range(1, len(active_platforms) + 1):
        for subset in combinations(active_platforms, size):
            starting: dict[str, NormalizedOffer] = {}
            for item_id in item_ids:
                offers = [
                    available[item_id][platform]
                    for platform in subset
                    if platform in available.get(item_id, {})
                ]
                if offers:
                    starting[item_id] = _choose_offer(offers)
            if require_complete and len(starting) != len(item_ids):
                continue
            if not starting:
                continue
            repaired, reasons = _repair_thresholds(
                starting, subset, available, item_by_id, fee_schedules, penalty_paise
            )
            evaluation = _evaluate(repaired, item_by_id, fee_schedules, penalty_paise)
            if evaluation is None:
                continue
            evaluations.append((evaluation, reasons))
    return evaluations


def _suggestions(
    evaluation: _Evaluation,
    fee_schedules: dict[str, FeeSchedule | None],
) -> tuple[str, ...]:
    suggestions: list[str] = []
    for platform, lines in evaluation.per_platform.items():
        schedule = fee_schedules.get(platform)
        if schedule is None:
            continue
        subtotal = lines["subtotal"]
        if schedule.free_delivery_threshold is not None:
            gap = rupees_to_paise(schedule.free_delivery_threshold) - subtotal
            if gap > 0:
                suggestions.append(
                    f"Add an eligible item for Rs {paise_to_rupees(gap)} to unlock free delivery at {platform}."
                )
        if schedule.small_cart_threshold is not None:
            gap = rupees_to_paise(schedule.small_cart_threshold) - subtotal
            if gap > 0:
                suggestions.append(
                    f"Add an eligible item for Rs {paise_to_rupees(gap)} to clear the small-cart threshold at {platform}."
                )
    return tuple(suggestions)


def optimize(
    items: tuple[ParsedItem, ...] | list[ParsedItem],
    platform_results: tuple[SearchResult, ...] | list[SearchResult],
    fee_schedules: dict[str, FeeSchedule | None],
    config: object | None = None,
) -> CartPlan:
    """Choose a deterministic fee-aware plan. Monetary results are integer paise."""
    item_tuple = tuple(sorted(items, key=lambda item: item.id))
    result_tuple = tuple(sorted(platform_results, key=lambda result: result.platform))
    item_by_id = {item.id: item for item in item_tuple}
    optimizer_config = OptimizerConfig.from_value(config)
    active_platforms = tuple(sorted({
        result.platform for result in result_tuple if _status_is_ok(result.store_status)
    }))
    if len(active_platforms) > 4:
        raise ValueError("optimizer supports at most four active platforms")
    available = _available_by_item(item_tuple, result_tuple)
    all_item_ids = tuple(
        item.id for item in item_tuple if available.get(item.id)
    )
    unavailable_reasons = {
        item.id: f"{item.name}: no in-stock offer on an available platform."
        for item in item_tuple if not available.get(item.id)
    }

    full = _subset_evaluations(
        active_platforms,
        all_item_ids,
        available,
        item_by_id,
        fee_schedules,
        optimizer_config.multi_platform_penalty_paise,
        require_complete=True,
    ) if all_item_ids else []

    chosen_result: _Evaluation | None
    reasons: tuple[str, ...]
    if full:
        chosen_result, reasons = min(full, key=lambda pair: _evaluation_key(pair[0]))
    else:
        partial = _subset_evaluations(
            active_platforms,
            all_item_ids,
            available,
            item_by_id,
            fee_schedules,
            optimizer_config.multi_platform_penalty_paise,
            require_complete=False,
        ) if all_item_ids else []
        if partial:
            chosen_result, reasons = min(
                partial,
                key=lambda pair: (
                    -len(pair[0].assignments),
                    *_evaluation_key(pair[0]),
                ),
            )
        else:
            chosen_result, reasons = None, ()

    # Best full single-platform landed total is the savings baseline.
    single_evaluations = [
        evaluation for evaluation, _ in full if len(evaluation.used_platforms) == 1
    ]
    best_single = min((evaluation.grand_total for evaluation in single_evaluations), default=None)
    naive_choice = {
        item_id: _choose_offer(list(offers.values()))
        for item_id, offers in available.items() if offers
    }
    price_floor = sum(offer.line_total_paise for offer in naive_choice.values())
    naive_subtotals: dict[str, int] = {}
    for offer in naive_choice.values():
        naive_subtotals[offer.platform] = naive_subtotals.get(offer.platform, 0) + offer.line_total_paise
    naive_fee_results = {
        platform: compute_fees(subtotal, fee_schedules.get(platform))
        for platform, subtotal in naive_subtotals.items()
    }
    naive_feasible = all(fee_result.feasible for fee_result in naive_fee_results.values())
    naive_with_fees = None
    if naive_feasible:
        naive_landed = sum(
            naive_subtotals[platform] + fee_result.total_paise
            for platform, fee_result in naive_fee_results.items()
        )
        naive_with_fees = naive_landed + optimizer_config.multi_platform_penalty_paise * max(
            0, len(naive_subtotals) - 1
        )

    if chosen_result is None:
        assignments: tuple[Assignment, ...] = ()
        per_platform: dict[str, dict[str, int]] = {}
        grand_total = 0
        objective = 0
        fees_estimated = False
        suggestions: tuple[str, ...] = ()
        fulfilled_ids: set[str] = set()
        result_reasons: tuple[str, ...] = ()
    else:
        assignments = tuple(
            Assignment(
                item_id=item.id,
                platform=offer.platform,
                sku_id=offer.candidate.sku_id,
                title=offer.candidate.title,
                qty=offer.packs_needed,
                unit_price=rupees_to_paise(offer.candidate.price),
                line_total=offer.line_total_paise,
                overbuy_qty=offer.overbuy_qty,
                overbuy_unit=offer.overbuy_unit,
            )
            for item, offer in chosen_result.assignments
        )
        per_platform = chosen_result.per_platform
        grand_total = chosen_result.grand_total
        objective = chosen_result.objective
        fees_estimated = chosen_result.fees_estimated
        suggestions = _suggestions(chosen_result, fee_schedules)
        fulfilled_ids = {assignment.item_id for assignment in assignments}
        result_reasons = reasons

    unfulfilled = list(unavailable_reasons.values())
    for item in item_tuple:
        if item.id not in fulfilled_ids and item.id not in unavailable_reasons:
            unfulfilled.append(
                f"{item.name}: no feasible assignment (a platform minimum order may not be met)."
            )
    best_single_saving = max(0, best_single - grand_total) if best_single is not None else 0
    naive_saving = (
        max(0, naive_with_fees - objective) if naive_with_fees is not None else 0
    )

    return CartPlan(
        assignments=assignments,
        per_platform=per_platform,
        grand_total=grand_total,
        baselines={
            "best_single_platform": best_single,
            "price_floor": price_floor,
            "naive_with_fees": naive_with_fees,
        },
        savings_vs_best_single=best_single_saving,
        savings_vs_naive=naive_saving,
        unfulfilled=tuple(unfulfilled),
        rationale=result_reasons,
        fees_estimated=fees_estimated,
        suggestions=suggestions,
        objective_paise=objective,
    )

