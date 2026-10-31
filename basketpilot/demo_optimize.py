"""Run a 20-item messy grocery list through parsing, mocks, and optimization."""

from __future__ import annotations

import asyncio

from basketpilot.models import SearchResult
from basketpilot.optimizer import OptimizerConfig, optimize
from basketpilot.optimizer.normalize import paise_to_rupees
from basketpilot.parser import parse_list
from basketpilot.platforms.mock import default_mock_adapters


DEMO_LIST = """
2 L doodh
1/2 kg tamato
1 kg aloo
dozen anday
500 g atta
1 kg pyaaz
1 L dahi
2 kg chawal
1 kg cheeni
250 g chai patti
1 kg namak
1 dozen kela
1 kg aple
200 g Amul paneer
1 kg bhindi
1 kg palak
500 g moong dal
1 L sarson tel
100 g haldi
200 ml nariyal pani
""".strip()


async def _build_results(parsed_items):
    adapters = default_mock_adapters()
    resolved = await asyncio.gather(
        *(adapter.resolve_items(parsed_items) for adapter in adapters)
    )
    statuses = await asyncio.gather(
        *(adapter.get_store_status() for adapter in adapters)
    )
    schedules = await asyncio.gather(
        *(adapter.get_fee_schedule() for adapter in adapters)
    )
    results = tuple(
        SearchResult(platform=adapter.name, results=items, store_status=status)
        for adapter, items, status in zip(adapters, resolved, statuses)
    )
    fee_schedules = {
        adapter.name: schedule for adapter, schedule in zip(adapters, schedules)
    }
    return adapters, results, fee_schedules


def _baseline_text(value: int | None) -> str:
    return "not feasible" if value is None else f"Rs {paise_to_rupees(value)}"


def main() -> None:
    parsed = parse_list(DEMO_LIST)
    adapters, results, fee_schedules = asyncio.run(_build_results(parsed.items))
    plan = optimize(
        parsed.items,
        results,
        fee_schedules,
        OptimizerConfig.from_value({"multi_platform_penalty": 10}),
    )
    items_by_id = {item.id: item for item in parsed.items}
    packs_by_sku = {
        candidate.sku_id: candidate.pack_size
        for search_result in results
        for item_candidates in search_result.results
        for candidate in item_candidates.candidates
    }

    print("BasketPilot v1 — illustrative mock data only")
    print(f"Requested distinct items: {len(parsed.items)}")
    print("| Item | Qty | Platform | Pack | Line price |")
    print("|---|---:|---|---|---:|")
    for assignment in plan.assignments:
        item = items_by_id[assignment.item_id]
        quantity = f"{item.qty:g}" + (f" {item.unit}" if item.unit else "")
        print(
            f"| {item.name} | {quantity} | {assignment.platform} "
            f"| {packs_by_sku.get(assignment.sku_id, 'unknown')} "
            f"| Rs {paise_to_rupees(assignment.line_total)} |"
        )

    print("\nPlatform totals and itemized fees")
    for platform, lines in plan.per_platform.items():
        print(f"- **{platform}:** subtotal Rs {paise_to_rupees(lines['subtotal'])}")
        for fee_name in ("delivery", "handling", "small_cart", "late_night", "other"):
            print(f"  - {fee_name.replace('_', ' ').title()}: Rs {paise_to_rupees(lines[fee_name])}")
        print(f"  - **Total: Rs {paise_to_rupees(lines['platform_total'])}**")
    print(f"- **Landed total:** Rs {paise_to_rupees(plan.grand_total)}")
    print(f"- **Objective (includes platform penalty):** Rs {paise_to_rupees(plan.objective_paise)}")

    print("\nUnfulfilled items")
    if plan.unfulfilled:
        for reason in plan.unfulfilled:
            print(f"- {reason}")
    else:
        print("- None")

    print("\nSuggestions")
    for suggestion in plan.suggestions or ("None",):
        print(f"- {suggestion}")
    print("\nRationale")
    for rationale in plan.rationale or ("No threshold-repair moves were needed.",):
        print(f"- {rationale}")

    print("\nBaselines and savings")
    print(f"- price_floor (fee-free): {_baseline_text(plan.baselines['price_floor'])}")
    print(f"- naive_with_fees (includes platform penalty): {_baseline_text(plan.baselines['naive_with_fees'])}")
    print(f"- best_single_platform: {_baseline_text(plan.baselines['best_single_platform'])}")
    print(f"- savings_vs_naive: Rs {paise_to_rupees(plan.savings_vs_naive)}")
    print(f"- savings_vs_best_single: Rs {paise_to_rupees(plan.savings_vs_best_single)}")
    if plan.fees_estimated:
        print("- Fee schedule contains estimates or omitted fee amounts.")


if __name__ == "__main__":
    main()
