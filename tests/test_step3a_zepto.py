from __future__ import annotations

import os
import time
import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from basketpilot.models import (
    Candidate, CartLine, CartSnapshot, FeeSchedule, ParsedItem, SearchResult, StoreStatus,
)
from basketpilot.optimizer import optimize
from basketpilot.platforms.zepto import (
    ZeptoAdapter, ZeptoPreview, _history_query, _past_order_product_names,
)
from basketpilot.platforms.zepto_client import (
    AddressRequired, AuthRequired, PolicyViolation, ToolError, ToolPolicy, ToolTimeout,
    TOOL_POLICY, ZeptoMCPClient, redact,
)
from scripts.record_zepto_fixtures import (
    _extract_address_id, _read, _saved_address_summaries, sanitize,
)


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

    async def test_mcp_tool_error_includes_tool_type_safe_message_and_shape(self):
        error_response = SimpleNamespace(
            isError=True,
            structuredContent=None,
            content=[SimpleNamespace(type="text", text="Invalid query for phone=9876543210")],
        )
        client = ZeptoMCPClient(timeout=1, retry_backoff=0)
        client._session = SimpleNamespace(call_tool=AsyncMock(return_value=error_response))
        client.connect = AsyncMock()
        with self.assertRaises(ToolError) as caught:
            await client.call("search_products", {"query": "milk"})
        detail = str(caught.exception)
        self.assertIn("tool=search_products", detail)
        self.assertIn("error_type=ToolError", detail)
        self.assertIn("Invalid query", detail)
        self.assertIn("response_structure=", detail)
        self.assertNotIn("9876543210", detail)
        self.assertEqual(client._session.call_tool.await_count, 2)

    async def test_zepto_error_prose_is_not_reported_as_success(self):
        error_response = SimpleNamespace(
            isError=False,
            structuredContent=None,
            content=[SimpleNamespace(
                type="text",
                text="Error: Store not selected. Please use get_location_serviceability or select_store first.",
            )],
        )
        client = ZeptoMCPClient(timeout=1, retry_backoff=0)
        client._session = SimpleNamespace(call_tool=AsyncMock(return_value=error_response))
        client.connect = AsyncMock()
        with self.assertRaises(AddressRequired) as caught:
            await client.call("search_multiple_products", {"queries": ["milk"]})
        self.assertIn("tool=search_multiple_products", str(caught.exception))
        self.assertIn("response_structure=", str(caught.exception))
        self.assertEqual(client._session.call_tool.await_count, 2)

    def test_captured_saved_address_identifier_parsing_does_not_disclose_it(self):
        response = 'Address: Home, ID: "01234567-89ab-cdef-0123-456789abcdef"'
        self.assertEqual(
            len(_extract_address_id(response) or ""),
            36,
        )
        self.assertEqual(_extract_address_id({"addresses": [{"addressId": "saved-id"}]}), "saved-id")

    def test_saved_address_summaries_handle_nested_json_text_and_hide_personal_fields(self):
        response = {"content": [{"type": "text", "text": json.dumps({
            "data": {"savedAddresses": [{
                "address_id": "01234567-89ab-cdef-0123-456789abcdef",
                "label": "Home",
                "formattedAddress": "42 Example Road, Bengaluru 560001",
                "contactName": "Private Person",
                "phoneNumber": "9876543210",
            }, {
                "addressId": "abcdef01-2345-6789-abcd-ef0123456789",
                "name": "Private Person",
                "formattedAddress": "Another full address",
            }]},
        })}]}
        self.assertEqual(_saved_address_summaries(response), [
            ("Home", "01234567-89ab-cdef-0123-456789abcdef"),
            ("saved address", "abcdef01-2345-6789-abcd-ef0123456789"),
        ])

    def test_saved_address_summary_handles_legacy_text_response(self):
        response = {"content": [{
            "type": "text",
            "text": 'Address: Work, ID: "01234567-89ab-cdef-0123-456789abcdef"',
        }]}
        self.assertEqual(_saved_address_summaries(response), [
            ("Work", "01234567-89ab-cdef-0123-456789abcdef"),
        ])

    async def test_read_recorder_reports_each_success_without_values(self):
        class Reader:
            async def call(self, tool, args):
                return {"addressId": "private-id", "formattedAddress": "private address"}

        from contextlib import redirect_stdout
        from io import StringIO

        output = StringIO()
        results = {}
        with redirect_stdout(output):
            result = await _read(Reader(), "list_saved_addresses", {}, results)
        self.assertEqual(result["addressId"], "private-id")
        self.assertIn("READ OK tool=list_saved_addresses", output.getvalue())
        self.assertNotIn("private-id", output.getvalue())
        self.assertNotIn("private address", output.getvalue())
        self.assertIn("list_saved_addresses", results)

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
        adapter = ZeptoAdapter(FakeClient(responses={"search_products": payload}))
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
        client = FakeClient(responses={"search_products": {
            "products": [{
                "id": "p1", "productVariantId": "v1", "storeProductId": "s1",
                "name": "Toned Milk", "price": 6250, "mrp": 7000,
                "packSize": "1 L", "availableQuantity": 4,
            }], "query": "milk", "totalCount": 1,
        }})
        adapter = ZeptoAdapter(client)
        result = await adapter.resolve_items((ParsedItem("i1", "milk"),))
        self.assertEqual(client.calls[0], ("get_past_order_items", {}))
        self.assertEqual(client.calls[1][0], "search_products")
        self.assertEqual(client.calls[1][1], {"query": "milk"})
        candidate = result[0].candidates[0]
        self.assertEqual((candidate.sku_id, candidate.price, candidate.pack_size), ("v1", 62.5, "1 L"))
        self.assertTrue(candidate.in_stock)
        self.assertEqual(candidate.max_qty, 4)
        self.assertIsNone(candidate.eta_min)
        self.assertIsNone(candidate.brand)

    async def test_past_order_prerequisite_uses_matching_product_name_without_logging_history(self):
        history = {"data": {"products": [{
            "productVariantId": "past-variant", "productName": "Amul Gold Milk 500 ml",
            "frequency": 4,
        }]}}
        client = FakeClient(responses={
            "get_past_order_items": history,
            "search_products": {
                "products": [], "query": "Amul Gold Milk 500 ml", "totalCount": 0,
            },
        })
        adapter = ZeptoAdapter(client)
        with self.assertNoLogs("basketpilot.platforms.zepto", level="INFO"):
            await adapter.resolve_items((ParsedItem("milk", "milk"),))
        self.assertEqual(_past_order_product_names(history), ("Amul Gold Milk 500 ml",))
        self.assertEqual(_history_query("milk", ("Amul Gold Milk 500 ml",)), "Amul Gold Milk 500 ml")
        self.assertEqual(client.calls, [
            ("get_past_order_items", {}),
            ("search_products", {"query": "Amul Gold Milk 500 ml"}),
        ])

    async def test_live_shaped_candidates_flow_into_existing_optimizer(self):
        adapter = ZeptoAdapter(FakeClient(responses={"search_products": {
            "products": [{
                "id": "catalog-1", "productVariantId": "variant-1",
                "storeProductId": "store-1", "name": "Milk 1 L",
                "price": 6250, "mrp": 7000, "packSize": "1 L",
                "availableQuantity": 3,
            }], "query": "milk", "totalCount": 1,
        }}))
        items = (ParsedItem("i1", "milk", qty=1, unit="L"),)
        candidates = await adapter.resolve_items(items)
        result = SearchResult(platform="zepto", results=candidates, store_status=StoreStatus.OK)
        plan = optimize(items, (result,), {"zepto": await adapter.get_fee_schedule()})
        self.assertEqual(plan.assignments[0].sku_id, "variant-1")
        self.assertEqual(plan.assignments[0].unit_price, 6250)
        self.assertTrue(plan.fees_estimated)

    async def test_search_skips_unavailable_and_non_cartable_products(self):
        adapter = ZeptoAdapter(FakeClient(responses={"search_products": {
            "products": [
                {"productVariantId": "oos", "storeProductId": "s-oos", "name": "Out", "price": 1000, "availableQuantity": 0},
                {"id": "catalog-only", "name": "No cart IDs", "price": 1000, "availableQuantity": 2},
                {"productVariantId": "live", "storeProductId": "s-live", "name": "In stock", "price": 1250, "availableQuantity": 2},
                {"productVariantId": "unknown-stock", "storeProductId": "s-unknown", "name": "Unknown stock", "price": 1400, "availableQuantity": None},
            ], "query": "milk", "totalCount": 4,
        }}))
        found = await adapter.resolve_items((ParsedItem("milk", "milk"),))
        self.assertEqual([row.sku_id for row in found[0].candidates], ["oos", "live", "unknown-stock"])
        self.assertFalse(found[0].candidates[0].in_stock)
        self.assertEqual(found[0].candidates[1].price, 12.5)
        self.assertFalse(found[0].candidates[2].in_stock)

    async def test_unexpected_search_structure_fails_closed(self):
        adapter = ZeptoAdapter(FakeClient(responses={"search_products": {"results": []}}))
        found = await adapter.resolve_items((ParsedItem("milk", "milk"),))
        self.assertEqual(found[0].candidates, ())
        self.assertEqual(await adapter.get_store_status(), StoreStatus.UNAVAILABLE)

    async def test_error_text_from_search_is_needs_address(self):
        adapter = ZeptoAdapter(FakeClient(responses={
            "search_products": "Error: Store not selected. Please use select_store first.",
        }))
        found = await adapter.resolve_items((ParsedItem("milk", "milk"),))
        self.assertEqual(found[0].candidates, ())
        self.assertEqual(await adapter.get_store_status(), StoreStatus.NEEDS_ADDRESS)

    async def test_auth_response_string_logs_category_without_raw_response(self):
        raw_error = "Authentication required for phone=9876543210 token=private-secret"
        adapter = ZeptoAdapter(FakeClient(responses={"search_products": raw_error}))
        with self.assertLogs("basketpilot.platforms.zepto", level="WARNING") as captured:
            found = await adapter.resolve_items((ParsedItem("milk", "milk"),))
        log = "\n".join(captured.output)
        self.assertEqual(found[0].candidates, ())
        self.assertEqual(await adapter.get_store_status(), StoreStatus.NEEDS_LOGIN)
        self.assertIn("tool=search_products", log)
        self.assertIn("error_type=AuthenticationRequired", log)
        self.assertIn("message=Zepto rejected product search as unauthenticated", log)
        self.assertIn("response_structure={'type': 'str'}", log)
        self.assertNotIn("Authentication required for phone", log)
        self.assertNotIn("9876543210", log)
        self.assertNotIn("private-secret", log)

    async def test_auth_failure_in_past_order_prerequisite_stops_before_search(self):
        raw_error = "Error: login required phone=9876543210 token=private-secret"
        client = FakeClient(responses={"get_past_order_items": raw_error})
        adapter = ZeptoAdapter(client)
        with self.assertLogs("basketpilot.platforms.zepto", level="WARNING") as captured:
            found = await adapter.resolve_items((ParsedItem("milk", "milk"),))
        log = "\n".join(captured.output)
        self.assertEqual(found[0].candidates, ())
        self.assertEqual(await adapter.get_store_status(), StoreStatus.NEEDS_LOGIN)
        self.assertEqual(client.calls, [("get_past_order_items", {})])
        self.assertIn("tool=get_past_order_items", log)
        self.assertIn("error_type=AuthenticationRequired", log)
        self.assertNotIn("9876543210", log)
        self.assertNotIn("private-secret", log)

    async def test_empty_search_section_is_a_valid_unavailable_item(self):
        client = FakeClient(responses={"search_multiple_products": {
            "sections": [
                {"query": "milk", "products": [{
                    "productVariantId": "v1", "storeProductId": "s1",
                    "name": "Milk", "price": 5000, "availableQuantity": 2,
                }]},
                {"query": "dragon fruit", "products": []},
            ],
        }, "select_saved_address": "Address selected."})
        adapter = ZeptoAdapter(client)
        await adapter.select_saved_address("configured-address")
        results = await adapter.resolve_items((
            ParsedItem("milk", "milk"), ParsedItem("fruit", "dragon fruit"),
        ))
        self.assertEqual(len(results[0].candidates), 1)
        self.assertEqual(results[1].candidates, ())
        self.assertEqual(await adapter.get_store_status(), StoreStatus.OK)

    async def test_configured_address_id_is_not_treated_as_selected_store(self):
        client = FakeClient(responses={"select_saved_address": "Address selected."})
        adapter = ZeptoAdapter(client, address_id="configured-address")
        self.assertEqual(await adapter.get_store_status(), StoreStatus.NEEDS_ADDRESS)
        self.assertEqual(await adapter.select_saved_address("configured-address"), StoreStatus.OK)
        self.assertEqual(await adapter.get_store_status(), StoreStatus.OK)

    async def test_auth_expired_search_returns_needs_login(self):
        class SearchAuthClient(FakeClient):
            async def call(self, tool, args=None):
                self.calls.append((tool, args or {}))
                if tool == "search_products":
                    raise AuthRequired(
                        "login", tool_name=tool,
                        response_structure={"is_error": True, "content": [{"type": "text", "text_length": 21}]},
                    )
                return {}

        adapter = ZeptoAdapter(SearchAuthClient())
        with self.assertLogs("basketpilot.platforms.zepto", level="WARNING") as captured:
            result = await adapter.resolve_items((ParsedItem("i1", "milk"),))
        self.assertEqual(result[0].candidates, ())
        self.assertEqual(await adapter.get_store_status(), StoreStatus.NEEDS_LOGIN)
        log = "\n".join(captured.output)
        self.assertIn("tool=search_products", log)
        self.assertIn("error_type=AuthRequired", log)
        self.assertIn("message=login", log)
        self.assertIn("response_structure=", log)

    async def test_search_tool_error_logs_sanitized_diagnostic_context(self):
        class SearchErrorClient(FakeClient):
            async def call(self, tool, args=None):
                self.calls.append((tool, args or {}))
                if tool == "search_products":
                    raise ToolError(
                        "Failed for phone=9876543210 token=private-token",
                        tool_name=tool,
                        response_structure={"is_error": True, "content": [{"type": "text"}]},
                    )
                return {}

        adapter = ZeptoAdapter(SearchErrorClient())
        with self.assertLogs("basketpilot.platforms.zepto", level="WARNING") as captured:
            result = await adapter.resolve_items((ParsedItem("i1", "milk"),))
        log = "\n".join(captured.output)
        self.assertEqual(result[0].candidates, ())
        self.assertEqual(await adapter.get_store_status(), StoreStatus.UNAVAILABLE)
        self.assertIn("tool=search_products", log)
        self.assertIn("error_type=ToolError", log)
        self.assertIn("message=", log)
        self.assertIn("response_structure=", log)
        self.assertNotIn("9876543210", log)
        self.assertNotIn("private-token", log)

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
        client = FakeClient(responses={"search_products": {
            "products": [{
                "productVariantId": "variant-1", "storeProductId": "store-1",
                "name": "Milk", "price": 6200, "packSize": "1 L",
                "availableQuantity": 3,
            }], "query": "milk", "totalCount": 1,
        }, "view_cart": [{"productVariantId": "variant-1", "quantity": 1}]})
        adapter = ZeptoAdapter(client, address_id="chosen")
        candidates = await adapter.resolve_items((ParsedItem("milk", "milk"),))
        offer = candidates[0].candidates[0]
        snapshot = await adapter.add_to_cart((CartLine(offer.sku_id, offer.title, 1, offer.price),))
        self.assertTrue(any("bill total" in error for error in snapshot.validation_errors))
        self.assertTrue(snapshot.fees.fees_estimated)
        self.assertEqual(client.calls[2][0], "update_cart")
        self.assertEqual(client.calls[2][1]["cartItems"][0]["quantity"], 1)
        self.assertFalse(client.calls[2][1]["replaceCart"])
        with self.assertRaises(ToolError):
            await adapter.preview_order()

        client.responses["view_cart"] = [{"productVariantId": "variant-1", "quantity": 2}]
        mismatch = await adapter.add_to_cart((CartLine(offer.sku_id, offer.title, 1, offer.price),))
        self.assertTrue(any("differ" in error for error in mismatch.validation_errors))

    async def test_cart_write_failure_attempts_readback_without_rollback(self):
        client = FakeClient(error=ToolError("simulated failure"))
        adapter = ZeptoAdapter(client)
        adapter._products["variant-1"] = {
            "productVariantId": "variant-1", "storeProductId": "store-1",
        }
        with self.assertRaises(ToolError) as caught:
            await adapter.add_to_cart((CartLine("variant-1", "Milk", 1, 62.5),))
        self.assertIn("may have partially applied", str(caught.exception))
        self.assertEqual([call[0] for call in client.calls], ["update_cart", "view_cart"])

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
