from __future__ import annotations

import time
import unittest
import asyncio

from basketpilot.models import (
    Candidate, FeeSchedule, ItemCandidates, ParsedItem, SearchResult, StoreStatus,
)
from basketpilot.optimizer import OptimizerConfig, optimize
from basketpilot.optimizer.fees import compute_fees
from basketpilot.optimizer.normalize import best_offer, normalize_candidate, paise_to_rupees
from basketpilot.parser import parse_list
from basketpilot.platforms.mock import default_mock_adapters
from basketpilot.demo_optimize import DEMO_LIST, _build_results


def candidate(sku: str, price: float, pack: str = "1 piece", **kwargs) -> Candidate:
    defaults = dict(title=sku, brand=None, pack_size=pack, price=price)
    defaults.update(kwargs)
    return Candidate(sku_id=sku, **defaults)


def result(platform: str, offers: dict[str, tuple[Candidate, ...]], status=StoreStatus.OK) -> SearchResult:
    return SearchResult(
        platform=platform,
        results=tuple(
            ItemCandidates(item_id=item_id, candidates=candidates)
            for item_id, candidates in offers.items()
        ),
        store_status=status,
    )


class OptimizerTests(unittest.TestCase):
    def assert_plan_invariant(self, plan) -> None:
        best_single = plan.baselines["best_single_platform"]
        naive_with_fees = plan.baselines["naive_with_fees"]
        if best_single is not None:
            self.assertLessEqual(plan.objective_paise, best_single)
        if naive_with_fees is not None:
            self.assertLessEqual(plan.objective_paise, naive_with_fees)
        self.assertGreaterEqual(plan.savings_vs_naive, 0)
        self.assertIsInstance(plan.grand_total, int)
        self.assertIsInstance(plan.objective_paise, int)
        for lines in plan.per_platform.values():
            self.assertTrue(all(isinstance(value, int) for value in lines.values()))
            self.assertEqual(
                lines["platform_total"],
                lines["subtotal"] + sum(
                    lines[name]
                    for name in ("delivery", "handling", "small_cart", "late_night", "other")
                ),
            )
        self.assertEqual(plan.grand_total, sum(
            lines["platform_total"] for lines in plan.per_platform.values()
        ))
        for platform, lines in plan.per_platform.items():
            self.assertEqual(
                lines["subtotal"],
                sum(line.line_total for line in plan.assignments if line.platform == platform),
            )

    def test_single_platform_only_and_all_money_is_integer_paise(self) -> None:
        items = [ParsedItem("a", "apple")]
        plan = optimize(
            items,
            [result("zepto", {"a": (candidate("a1", 10.10),)})],
            {"zepto": FeeSchedule(delivery=2.05, handling=1)},
        )
        self.assertEqual([assignment.platform for assignment in plan.assignments], ["zepto"])
        self.assertEqual(plan.grand_total, 1315)
        self.assertEqual(plan.per_platform["zepto"]["delivery"], 205)
        self.assert_plan_invariant(plan)

    def test_closed_platform_is_excluded(self) -> None:
        item = ParsedItem("a", "apple")
        plan = optimize(
            [item],
            [
                result("closed", {"a": (candidate("cheap", 1),)}, StoreStatus.CLOSED),
                result("open", {"a": (candidate("open", 20),)}),
            ],
            {"closed": FeeSchedule(), "open": FeeSchedule()},
        )
        self.assertEqual(plan.assignments[0].platform, "open")
        self.assertNotIn("closed", plan.per_platform)
        self.assert_plan_invariant(plan)

    def test_platform_below_minimum_order_is_infeasible(self) -> None:
        item = ParsedItem("a", "apple")
        plan = optimize(
            [item],
            [result("only", {"a": (candidate("a1", 10),)})],
            {"only": FeeSchedule(min_order=20)},
        )
        self.assertFalse(plan.assignments)
        self.assertIn("minimum order", plan.unfulfilled[0])
        self.assert_plan_invariant(plan)

    def test_unavailable_item_does_not_block_rest_of_plan(self) -> None:
        items = [ParsedItem("a", "apple"), ParsedItem("b", "banana")]
        plan = optimize(
            items,
            [result("p", {"a": (candidate("a1", 10),)})],
            {"p": FeeSchedule()},
        )
        self.assertEqual([assignment.item_id for assignment in plan.assignments], ["a"])
        self.assertTrue(any("banana" in reason and "no in-stock offer" in reason for reason in plan.unfulfilled))
        self.assert_plan_invariant(plan)

    def test_unique_platform_offer_forces_that_platform(self) -> None:
        items = [ParsedItem("a", "apple"), ParsedItem("b", "banana")]
        plan = optimize(
            items,
            [
                result("p1", {"a": (candidate("a1", 10),), "b": (candidate("b1", 20),)}),
                result("p2", {"b": (candidate("b2", 19),)}),
            ],
            {"p1": FeeSchedule(), "p2": FeeSchedule()},
            OptimizerConfig(0),
        )
        self.assertEqual({line.item_id: line.platform for line in plan.assignments}, {"a": "p1", "b": "p2"})
        self.assert_plan_invariant(plan)

    def test_free_delivery_threshold_below_equal_and_above(self) -> None:
        schedule = FeeSchedule(delivery=10, free_delivery_threshold=100)
        below = compute_fees(9900, schedule)
        equal = compute_fees(10000, schedule)
        above = compute_fees(10100, schedule)
        self.assertEqual(below.lines_paise["delivery"], 1000)
        self.assertEqual(equal.lines_paise["delivery"], 0)
        self.assertEqual(above.lines_paise["delivery"], 0)
        self.assert_plan_invariant(optimize(
            [ParsedItem("x", "x")],
            [result("p", {"x": (candidate("x", 99),)})],
            {"p": schedule},
        ))

    def test_small_cart_fee_applies_strictly_below_threshold(self) -> None:
        schedule = FeeSchedule(small_cart=5, small_cart_threshold=100)
        self.assertEqual(compute_fees(9999, schedule).lines_paise["small_cart"], 500)
        self.assertEqual(compute_fees(10000, schedule).lines_paise["small_cart"], 0)

    def test_threshold_suggestion_never_adds_a_filler_to_plan(self) -> None:
        plan = optimize(
            [ParsedItem("x", "x")],
            [result("p", {"x": (candidate("x", 99),)})],
            {"p": FeeSchedule(delivery=10, free_delivery_threshold=100)},
        )
        self.assertEqual(len(plan.assignments), 1)
        self.assertTrue(any("eligible item for Rs 1.00" in text for text in plan.suggestions))
        self.assert_plan_invariant(plan)

    def test_split_is_selected_when_savings_exceed_fees_and_penalty(self) -> None:
        items = [ParsedItem("a", "apple"), ParsedItem("b", "banana")]
        results = [
            result("p1", {"a": (candidate("a1", 10),), "b": (candidate("b1", 12),)}),
            result("p2", {"a": (candidate("a2", 12),), "b": (candidate("b2", 10),)}),
        ]
        split = optimize(
            items, results,
            {"p1": FeeSchedule(delivery=1), "p2": FeeSchedule(delivery=1)},
            OptimizerConfig(0),
        )
        self.assertEqual({assignment.platform for assignment in split.assignments}, {"p1", "p2"})
        self.assertGreater(split.savings_vs_best_single, 0)
        self.assert_plan_invariant(split)

    def test_threshold_repair_moves_requested_item_and_explains_saving(self) -> None:
        items = [
            ParsedItem("a", "milk"),
            ParsedItem("b", "rice"),
            ParsedItem("c", "onion"),
        ]
        plan = optimize(
            items,
            [
                result("zepto", {
                    "a": (candidate("za", 60),),
                    "b": (candidate("zb", 50),),
                    "c": (candidate("zc", 1),),
                }),
                result("instamart", {
                    "a": (candidate("ia", 50),),
                    "b": (candidate("ib", 55),),
                }),
            ],
            {
                "zepto": FeeSchedule(delivery=0, handling=0, small_cart=0, late_night=0, other=0),
                "instamart": FeeSchedule(
                    delivery=20, handling=0, small_cart=0, late_night=0, other=0,
                    free_delivery_threshold=100,
                ),
            },
            OptimizerConfig(0),
        )
        self.assertEqual({line.item_id: line.platform for line in plan.assignments},
                         {"a": "instamart", "b": "instamart", "c": "zepto"})
        self.assertTrue(any(
            "Moved rice to instamart to cross the free-delivery threshold" in reason
            for reason in plan.rationale
        ))
        self.assert_plan_invariant(plan)

    def test_split_is_rejected_when_fees_and_penalty_exceed_savings(self) -> None:
        items = [ParsedItem("a", "apple"), ParsedItem("b", "banana")]
        results = [
            result("p1", {"a": (candidate("a1", 10),), "b": (candidate("b1", 12),)}),
            result("p2", {"a": (candidate("a2", 12),), "b": (candidate("b2", 10),)}),
        ]
        plan = optimize(
            items, results,
            {"p1": FeeSchedule(delivery=1), "p2": FeeSchedule(delivery=1)},
            OptimizerConfig(multi_platform_penalty_paise=500),
        )
        self.assertEqual({assignment.platform for assignment in plan.assignments}, {"p1"})
        self.assertEqual(plan.objective_paise, plan.baselines["best_single_platform"])
        self.assert_plan_invariant(plan)

    def test_unit_price_and_pack_normalization_500g_vs_1kg(self) -> None:
        item = ParsedItem("a", "rice", 1, "kg")
        small = normalize_candidate("p", item, candidate("small", 40, "500 g"))
        large = normalize_candidate("p", item, candidate("large", 90, "1 kg"))
        self.assertEqual(small.packs_needed, 2)
        self.assertEqual(small.line_total_paise, 8000)
        self.assertEqual(small.unit_price_paise, 800)
        self.assertEqual(small.canonical_unit, "100 g")
        self.assertEqual(large.unit_price_paise, 900)
        self.assertEqual(best_offer(item, "p", (
            candidate("large", 90, "1 kg"), candidate("small", 40, "500 g")
        )).candidate.sku_id, "small")
        self.assertEqual(small.overbuy_qty, 0)

    def test_unit_price_and_overbuy_6_by_200ml_vs_1l(self) -> None:
        item = ParsedItem("milk", "milk", 1, "L")
        multipack = normalize_candidate("p", item, candidate("multi", 3, "6 x 200 ml"))
        one_litre = normalize_candidate("p", item, candidate("one", 2.60, "1 L"))
        self.assertEqual(multipack.packs_needed, 1)
        self.assertEqual(multipack.unit_price_paise, 250)
        self.assertEqual(multipack.canonical_unit, "L")
        self.assertAlmostEqual(multipack.overbuy_qty, 0.2)
        self.assertEqual(one_litre.unit_price_paise, 260)

    def test_brand_stock_and_max_quantity_filters(self) -> None:
        item = ParsedItem("a", "tea", 2, "kg", brand="Preferred")
        candidates = (
            candidate("out", 1, "1 kg", in_stock=False, brand="Preferred"),
            candidate("wrong-brand", 2, "1 kg", brand="Other"),
            candidate("over-limit", 3, "1 kg", max_qty=1, brand="Preferred"),
        )
        self.assertIsNone(best_offer(item, "p", candidates))

    def test_ties_prefer_score_then_lowest_sku(self) -> None:
        item = ParsedItem("a", "apple")
        selected = best_offer(item, "p", (
            candidate("z", 1, match_score=0.5),
            candidate("b", 1, match_score=0.9),
            candidate("a", 1, match_score=0.9),
        ))
        self.assertEqual(selected.candidate.sku_id, "a")

    def test_shuffled_inputs_produce_identical_plan_and_savings_baselines(self) -> None:
        items = [ParsedItem("a", "apple"), ParsedItem("b", "banana"), ParsedItem("c", "carrot")]
        results = [
            result("p1", {"a": (candidate("a1", 10),), "b": (candidate("b1", 13),)}),
            result("p2", {"a": (candidate("a2", 12),), "b": (candidate("b2", 10),),
                          "c": (candidate("c2", 7),)}),
        ]
        fees = {"p1": FeeSchedule(delivery=2), "p2": FeeSchedule(delivery=3)}
        expected = optimize(items, results, fees, OptimizerConfig(0))
        shuffled = optimize(list(reversed(items)), list(reversed(results)), fees, OptimizerConfig(0))
        self.assertEqual(expected, shuffled)
        self.assert_plan_invariant(expected)

    def test_missing_fee_schedule_marks_estimate_and_missing_item_is_unfulfilled(self) -> None:
        plan = optimize(
            [ParsedItem("a", "apple")],
            [result("p", {"a": (candidate("a1", 1),)})],
            {},
        )
        self.assertTrue(plan.fees_estimated)
        self.assertEqual(plan.per_platform["p"]["delivery"], 0)
        self.assertTrue(all(isinstance(value, int) for value in plan.per_platform["p"].values()))
        self.assertEqual(paise_to_rupees(plan.grand_total), "1.00")
        self.assert_plan_invariant(plan)

    def test_schedule_with_unreported_fee_amounts_marks_estimate(self) -> None:
        plan = optimize(
            [ParsedItem("a", "apple")],
            [result("p", {"a": (candidate("a1", 1),)})],
            {"p": FeeSchedule(delivery=0)},
        )
        self.assertTrue(plan.fees_estimated)
        self.assertEqual(plan.grand_total, 100)
        self.assert_plan_invariant(plan)

    def test_price_floor_and_naive_with_fees_baselines_are_reported(self) -> None:
        plan = optimize(
            [ParsedItem("a", "apple")],
            [result("p", {"a": (candidate("a1", 2),)})],
            {"p": FeeSchedule(delivery=1)},
        )
        self.assertEqual(plan.baselines["price_floor"], 200)
        self.assertEqual(plan.baselines["naive_with_fees"], 300)
        self.assertEqual(plan.savings_vs_naive, 0)
        self.assert_plan_invariant(plan)

    def test_60_items_four_platforms_under_200ms(self) -> None:
        items = [ParsedItem(f"i{index:02}", "staple") for index in range(60)]
        results = [
            result(
                f"p{platform}",
                {
                    item.id: (candidate(f"{item.id}-{platform}", 10 + platform),)
                    for item in items
                },
            )
            for platform in range(4)
        ]
        fees = {f"p{platform}": FeeSchedule(delivery=15 + platform) for platform in range(4)}
        started = time.perf_counter()
        plan = optimize(items, results, fees, OptimizerConfig(100))
        elapsed = time.perf_counter() - started
        self.assertEqual(len(plan.assignments), 60)
        self.assertLess(elapsed, 0.2, f"optimizer took {elapsed:.3f}s")
        self.assert_plan_invariant(plan)

    def test_mock_catalog_has_required_edge_cases_and_30_plus_skus(self) -> None:
        adapters = default_mock_adapters()
        self.assertGreaterEqual(sum(len(adapter._offers) for adapter in adapters), 30)

        async def resolve_cases():
            items = (
                ParsedItem("palak", "palak", 1, "kg"),
                ParsedItem("dahi", "dahi", 1, "L"),
                ParsedItem("coconut", "nariyal pani", 200, "ml"),
                ParsedItem("milk", "doodh", 1, "L"),
                ParsedItem("atta", "atta", 1, "kg"),
                ParsedItem("paneer", "Amul paneer", 200, "g"),
            )
            return items, await asyncio.gather(
                *(adapter.resolve_items(items) for adapter in adapters)
            )

        items, per_platform = asyncio.run(resolve_cases())
        by_platform = {
            adapter.name: {item.item_id: item.candidates for item in resolved}
            for adapter, resolved in zip(adapters, per_platform)
        }
        self.assertTrue(by_platform["zepto-mock"]["palak"])
        self.assertFalse(by_platform["instamart-mock"]["palak"])
        self.assertTrue(by_platform["instamart-mock"]["dahi"])
        self.assertFalse(by_platform["zepto-mock"]["dahi"])
        self.assertTrue(all(
            candidates and not candidates[0].in_stock
            for candidates in (
                by_platform["zepto-mock"]["coconut"],
                by_platform["instamart-mock"]["coconut"],
            )
        ))
        self.assertTrue(any(
            item.brand == "Amul"
            for item in by_platform["zepto-mock"]["paneer"]
        ))
        milk_packs = {
            offer.candidate.pack_size for adapter in adapters for offer in adapter._offers
            if "milk" in offer.keywords
        }
        atta_packs = {
            offer.candidate.pack_size for adapter in adapters for offer in adapter._offers
            if "atta" in offer.keywords
        }
        self.assertIn("6 x 200 ml", milk_packs)
        self.assertIn("1 L", milk_packs)
        self.assertIn("500 g", atta_packs)
        self.assertIn("1 kg", atta_packs)

    def test_twenty_distinct_messy_demo_items_and_fee_gap(self) -> None:
        parsed = parse_list(DEMO_LIST)
        self.assertEqual(len(parsed.items), 20)
        self.assertEqual(len({" ".join(item.name.casefold().split()) for item in parsed.items}), 20)
        self.assertFalse(parsed.ambiguities)

        adapters, results, schedules = asyncio.run(_build_results(parsed.items))
        plan = optimize(
            parsed.items,
            results,
            schedules,
            OptimizerConfig.from_value({"multi_platform_penalty": 10}),
        )
        self.assert_plan_invariant(plan)
        self.assertEqual(len(plan.assignments), 19)
        self.assertTrue(any("nariyal pani" in reason for reason in plan.unfulfilled))
        zepto_subtotal = plan.per_platform["zepto-mock"]["subtotal"]
        zepto_threshold = int(schedules["zepto-mock"].free_delivery_threshold * 100)
        self.assertIn(zepto_threshold - zepto_subtotal, range(2000, 5001))


if __name__ == "__main__":
    unittest.main()
