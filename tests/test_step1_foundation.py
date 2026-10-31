from __future__ import annotations

import asyncio
import unittest

from basketpilot.models import CartLine, ParsedItem, StoreStatus
from basketpilot.parser import parse_list
from basketpilot.platforms.mock import default_mock_adapters


class FoundationTests(unittest.TestCase):
    def test_parser_handles_quantities_units_and_separators(self) -> None:
        parsed = parse_list("2L milk, 1 kg onions\neggs x12")

        self.assertEqual(
            [(item.name, item.qty, item.unit) for item in parsed.items],
            [("milk", 2.0, "L"), ("onions", 1.0, "kg"), ("eggs", 12.0, "piece")],
        )
        self.assertFalse(parsed.ambiguities)

    def test_parser_preserves_unparsed_lines_as_ambiguities(self) -> None:
        parsed = parse_list("milk, 0 kg onions")

        self.assertEqual(len(parsed.items), 1)
        self.assertEqual(parsed.ambiguities, ("0 kg onions",))

    def test_mock_adapters_return_normalized_candidates(self) -> None:
        zepto, instamart = default_mock_adapters()

        async def resolve_both():
            return await asyncio.gather(
                zepto.resolve_items((ParsedItem("item-1", "milk", 2, "L"), ParsedItem("item-2", "bananas"))),
                instamart.resolve_items((ParsedItem("item-1", "milk", 2, "L"), ParsedItem("item-2", "bananas"))),
            )

        zepto_results, instamart_results = asyncio.run(resolve_both())
        self.assertEqual(zepto_results[0].candidates[0].sku_id, "z-milk-1l")
        self.assertEqual(instamart_results[0].candidates[0].sku_id, "i-milk-1l")
        self.assertEqual(zepto_results[1].candidates, ())
        self.assertEqual(asyncio.run(zepto.get_store_status()), StoreStatus.OK)

    def test_mock_cart_snapshot_includes_fee_breakdown_and_threshold(self) -> None:
        zepto, _ = default_mock_adapters()
        snapshot = asyncio.run(
            zepto.add_to_cart((CartLine("z-milk-1l", "Toned Milk 1 L", 4, 62),))
        )

        self.assertEqual(snapshot.subtotal, 248)
        self.assertEqual(snapshot.fees.delivery, 0)
        self.assertEqual(snapshot.fees.handling, 5)
        self.assertEqual(snapshot.total, 253)

