from __future__ import annotations

import os
import time
import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from basketpilot.models import (
    Candidate, CartLine, CartSnapshot, FeeSchedule, ParsedItem, StoreStatus,
)
from basketpilot.platforms.zepto import ZeptoAdapter, ZeptoPreview
from basketpilot.platforms.zepto_client import (
    AddressRequired, AuthRequired, PolicyViolation, ToolError, ToolPolicy, ToolTimeout,
    TOOL_POLICY, ZeptoMCPClient, redact,
)
from scripts.record_zepto_fixtures import sanitize


class FakeClient:
    def __init__(self, *, responses=None, error=None):
        self.responses = responses or {}
        self.error = error
        self.calls = []

    async def call(self, tool, args=None):
        self.calls.append((tool, args or {}))
        if self.error:
            raise self.error
        return self.responses.get(tool)

    async def call_money(self, tool, args, *, authorized):
        self.calls.append((tool, args, authorized))
        return {"mocked": True}


class ZeptoPolicyTests(unittest.IsolatedAsyncioTestCase):
    def test_all_23_tools_have_expected_classification(self):
        self.assertEqual(len(TOOL_POLICY), 23)
        schema_file = Path(__file__).parents[1] / "docs" / "zepto_tools.json"
        schema_names = {tool["name"] for tool in json.loads(schema_file.read_text())}
        self.assertEqual(set(TOOL_POLICY), schema_names)
        self.assertEqual(sum(p is ToolPolicy.READ for p in TOOL_POLICY.values()), 13)
        self.assertEqual(sum(p is ToolPolicy.WRITE_SAFE for p in TOOL_POLICY.values()), 3)
        self.assertEqual(sum(p is ToolPolicy.BLOCKED_ALWAYS for p in TOOL_POLICY.values()), 4)
        self.assertEqual(sum(p is ToolPolicy.MONEY for p in TOOL_POLICY.values()), 3)
        self.assertIs(TOOL_POLICY["zepto_shop"], ToolPolicy.READ)

    async def test_direct_money_and_blocked_calls_are_rejected_without_connecting(self):
        client = ZeptoMCPClient(command="/no/such/program")
        for tool in (
            "create_order", "create_online_payment_order", "create_wallet_order",
            "create_upi_reserve_pay_order", "add_saved_address", "update_user_name",
            "update_drop_zone",
        ):
            with self.subTest(tool=tool), self.assertRaises(PolicyViolation):
                await client.call(tool, {})

    async def test_zepto_shop_only_accepts_read_intents(self):
        client = ZeptoMCPClient(command="/no/such/program")
        with self.assertRaises(PolicyViolation):
            await client.call("zepto_shop", {"intent": "order"})

    async def test_read_timeout_retries_once_but_write_timeout_does_not(self):
        success = SimpleNamespace(isError=False, structuredContent={"ok": True}, content=[])
        reader = ZeptoMCPClient(timeout=1, retry_backoff=0)
        reader._session = SimpleNamespace(call_tool=AsyncMock(side_effect=[TimeoutError(), success]))
        reader.connect = AsyncMock()
        reader.close = AsyncMock()
        self.assertEqual(await reader.call("view_cart"), {"ok": True})
        self.assertEqual(reader._session.call_tool.await_count, 2)
        reader.close.assert_awaited_once()

        writer = ZeptoMCPClient(timeout=1, retry_backoff=0)
        writer._session = SimpleNamespace(call_tool=AsyncMock(side_effect=TimeoutError()))
        writer.connect = AsyncMock()
        with self.assertRaises(ToolTimeout):
            await writer.call("update_cart", {"deviceId": "local", "cartItems": []})
        self.assertEqual(writer._session.call_tool.await_count, 1)

    async def test_read_transport_error_reconnects_at_most_once(self):
        success = SimpleNamespace(isError=False, structuredContent={"ok": True}, content=[])
        client = ZeptoMCPClient(timeout=1, retry_backoff=0)
        client._session = SimpleNamespace(call_tool=AsyncMock(side_effect=[OSError("broken pipe"), success]))
        client.connect = AsyncMock()
        client.close = AsyncMock()
        self.assertEqual(await client.call("view_cart"), {"ok": True})
        self.assertEqual(client._session.call_tool.await_count, 2)
        client.close.assert_awaited_once()

    def test_log_redaction_masks_account_fields_and_phone(self):
        line = redact("name=Ramesh address=42 Main Street token=abc123 phone=9876543210")
        for secret in ("Ramesh", "42 Main Street", "abc123", "9876543210"):
            self.assertNotIn(secret, line)

    def test_fixture_sanitizer_keeps_pincode_only_for_address(self):
        clean = sanitize({
            "addressId": "addr-1", "formattedAddress": "12 Example Road, Bengaluru 560001",
            "contactName": "Ramesh", "contactNumber": "9876543210",
        }, tool="list_saved_addresses")
        self.assertEqual(clean["addressId"], "[REDACTED]")
        self.assertEqual(clean["formattedAddress"], "pincode:560001")
        self.assertEqual(clean["contactName"], "[REDACTED]")
        self.assertEqual(clean["contactNumber"], "[REDACTED]")


class ZeptoAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_recorded_search_error_fixture_maps_store_to_needs_address(self):
        fixture = Path(__file__).parent / "fixtures" / "zepto" / "search_multiple_products.json"
        payload = json.loads(fixture.read_text())
        adapter = ZeptoAdapter(FakeClient(responses={"search_multiple_products": payload}))
        items = await adapter.resolve_items((ParsedItem("i1", "milk"),))
        self.assertEqual(items[0].candidates, ())
        self.assertEqual(await adapter.get_store_status(), StoreStatus.NEEDS_ADDRESS)

    def test_recorded_address_fixture_is_pincode_only(self):
        fixture = Path(__file__).parent / "fixtures" / "zepto" / "list_saved_addresses.json"
        payload = json.loads(fixture.read_text())
        self.assertIn("560091", payload)
        self.assertNotIn("Maruti", payload)
        self.assertNotIn("828f96e2", payload)

    async def test_search_schema_maps_prices_in_paise_to_candidate_rupees(self):
        client = FakeClient(responses={"search_multiple_products": {
            "sections": [{"query": "milk", "products": [{
                "id": "p1", "productVariantId": "v1", "storeProductId": "s1",
                "name": "Toned Milk", "price": 6250, "mrp": 7000,
                "packSize": "1 L", "availableQuantity": 4,
            }], "totalCount": 1}], "totalSections": 1,
        }})
        adapter = ZeptoAdapter(client)
        result = await adapter.resolve_items((ParsedItem("i1", "milk"),))
        self.assertEqual(client.calls[0][0], "search_multiple_products")
        self.assertEqual(client.calls[0][1], {"queries": ["milk"]})
        candidate = result[0].candidates[0]
        self.assertEqual((candidate.sku_id, candidate.price, candidate.pack_size), ("v1", 62.5, "1 L"))
        self.assertTrue(candidate.in_stock)
        self.assertEqual(candidate.max_qty, 4)
        self.assertIsNone(candidate.eta_min)

    async def test_auth_expired_search_returns_needs_login(self):
        adapter = ZeptoAdapter(FakeClient(error=AuthRequired("login")))
        result = await adapter.resolve_items((ParsedItem("i1", "milk"),))
        self.assertEqual(result[0].candidates, ())
        self.assertEqual(await adapter.get_store_status(), StoreStatus.NEEDS_LOGIN)

    async def test_no_address_maps_to_needs_address(self):
        adapter = ZeptoAdapter(FakeClient())
        self.assertEqual(await adapter.get_store_status(), StoreStatus.NEEDS_ADDRESS)

    async def test_address_selection_and_missing_address_notice(self):
        adapter = ZeptoAdapter(FakeClient(responses={"select_saved_address": "Address selected."}))
        self.assertEqual(await adapter.select_saved_address("saved-id"), StoreStatus.OK)
        self.assertEqual(await adapter.get_store_status(), StoreStatus.OK)
        needs_address = ZeptoAdapter(FakeClient(error=AddressRequired("address")))
        result = await needs_address.resolve_items((ParsedItem("i1", "milk"),))
        self.assertEqual(result[0].candidates, ())
        self.assertEqual(await needs_address.get_store_status(), StoreStatus.NEEDS_ADDRESS)

    async def test_cart_validates_quantities_and_fails_closed_without_bill_fields(self):
        client = FakeClient(responses={"search_multiple_products": {
            "sections": [{"query": "milk", "products": [{
                "productVariantId": "variant-1", "storeProductId": "store-1",
                "name": "Milk", "price": 6200, "packSize": "1 L",
                "availableQuantity": 3,
            }]}],
        }, "view_cart": [{"productVariantId": "variant-1", "quantity": 1}]})
        adapter = ZeptoAdapter(client, address_id="chosen")
        candidates = await adapter.resolve_items((ParsedItem("milk", "milk"),))
        offer = candidates[0].candidates[0]
        snapshot = await adapter.add_to_cart((CartLine(offer.sku_id, offer.title, 1, offer.price),))
        self.assertTrue(any("bill total" in error for error in snapshot.validation_errors))
        self.assertTrue(snapshot.fees.fees_estimated)
        self.assertEqual(client.calls[1][0], "update_cart")
        self.assertEqual(client.calls[1][1]["cartItems"][0]["quantity"], 1)
        with self.assertRaises(ToolError):
            await adapter.preview_order()

        client.responses["view_cart"] = [{"productVariantId": "variant-1", "quantity": 2}]
        mismatch = await adapter.add_to_cart((CartLine(offer.sku_id, offer.title, 1, offer.price),))
        self.assertTrue(any("differ" in error for error in mismatch.validation_errors))

    async def test_preview_token_expiry_changed_cart_and_wrong_platform(self):
        client = FakeClient()
        adapter = ZeptoAdapter(client, address_id="address-local", preview_ttl_seconds=30)
        line = CartLine("sku-1", "milk", 1, 62.5)
        adapter._cart = (line,)
        adapter._products["sku-1"] = {"productVariantId": "sku-1"}
        # Isolate the guarded call path with a locally seeded snapshot; the live view_cart
        # output schema currently lacks a documented bill-total field, so no live preview
        # can be issued until a real response fixture establishes that field.
        adapter._snapshot = CartSnapshot("zepto", (line,), 62.5, FeeSchedule(), 62.5, ())
        client.responses["view_cart"] = [{"productVariantId": "sku-1", "quantity": 1}]
        client.responses["get_payment_methods"] = {"mock": "recorded-choice"}
        old = os.environ.get("BP_ALLOW_ORDERS")
        os.environ["BP_ALLOW_ORDERS"] = "true"
        try:
            preview = await adapter.preview_order()
            self.assertTrue(preview.preview_token)
            adapter._previews["expired"] = ZeptoPreview(
                "zepto", (line,), 6250, "address-local", "expired", 0,
                preview.cart_hash,
            )
            with self.assertRaises(ToolError):
                await adapter.place_order("expired", confirmed=True)
            adapter._previews["wrong"] = ZeptoPreview(
                "instamart", (line,), 6250, "address-local", "wrong", time.monotonic() + 60,
                preview.cart_hash,
            )
            with self.assertRaises(ToolError):
                await adapter.place_order("wrong", confirmed=True)
            adapter._cart = (CartLine("sku-1", "milk", 2, 62.5),)
            with self.assertRaises(ToolError):
                await adapter.place_order(preview.preview_token, confirmed=True)
            adapter._cart = (line,)
            response = await adapter.place_order(preview.preview_token, confirmed=True)
            self.assertEqual(response, {"mocked": True})
            self.assertEqual(client.calls[-1][0], "create_order")
            self.assertTrue(client.calls[-1][2])
        finally:
            if old is None:
                os.environ.pop("BP_ALLOW_ORDERS", None)
            else:
                os.environ["BP_ALLOW_ORDERS"] = old

    async def test_order_placement_is_disabled_by_default(self):
        client = FakeClient()
        adapter = ZeptoAdapter(client)
        old = os.environ.pop("BP_ALLOW_ORDERS", None)
        try:
            with self.assertRaises(ToolError):
                await adapter.place_order("anything", confirmed=True)
            self.assertFalse(client.calls)
        finally:
            if old is not None:
                os.environ["BP_ALLOW_ORDERS"] = old


if __name__ == "__main__":
    unittest.main()
