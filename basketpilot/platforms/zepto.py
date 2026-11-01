"""Zepto adapter. Response handling is limited to documented tool fields."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from basketpilot.models import (
    Candidate, CartLine, CartSnapshot, FeeSchedule, ItemCandidates, ParsedItem,
    StoreStatus,
)
from basketpilot.platforms.zepto_client import (
    AddressRequired, AuthRequired, StoreClosed, Timeout, ToolError, Unserviceable,
    ZeptoMCPClient, _shape,
)
from basketpilot.optimizer.normalize import rupees_to_paise

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ZeptoPreview:
    platform: str
    lines: tuple[CartLine, ...]
    total_paise: int
    address_id: str
    preview_token: str
    expires_at: float
    cart_hash: str


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json", by_alias=True)
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _product_price_rupees(product: dict[str, Any]) -> float | None:
    # search_products and search_multiple_products explicitly return price in paise.
    value = product.get("price")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(Decimal(str(value)) / 100)
    return None


def _pack_size(product: dict[str, Any]) -> str:
    value = product.get("packSize")
    return value.strip() if isinstance(value, str) and value.strip() else "unknown pack"


def _products_by_section(payload: Any) -> list[tuple[str, dict[str, Any]]] | None:
    """Parse only the documented search_multiple_products response shape."""
    data = _to_dict(payload)
    sections = data.get("sections")
    if not isinstance(sections, list):
        return None
    found: list[tuple[str, dict[str, Any]]] = []
    for section in sections:
        section_data = _to_dict(section)
        query, products = section_data.get("query"), section_data.get("products")
        if not isinstance(query, str) or not isinstance(products, list):
            return None
        for product in products:
            product_data = _to_dict(product)
            if not product_data:
                logger.warning("Zepto search response contained a non-object product; skipping it.")
                continue
            found.append((query, product_data))
    return found


def _past_order_product_names(payload: Any) -> tuple[str, ...]:
    """Extract names only from records carrying the documented productVariantId."""
    names: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            normalized = {
                re.sub(r"[^a-z0-9]", "", str(key).casefold()): item
                for key, item in value.items()
            }
            variant_id = normalized.get("productvariantid")
            if isinstance(variant_id, str) and variant_id:
                preferred = [
                    item for key, item in normalized.items()
                    if key in {"name", "productname", "title"}
                    and isinstance(item, str) and item.strip()
                ]
                if preferred:
                    name = preferred[0].strip()
                else:
                    # Output schema is not published; use a sole non-ID string sibling
                    # from a documented product record rather than assuming a wrapper.
                    strings = [
                        item.strip() for key, item in normalized.items()
                        if key not in {"productvariantid", "variantid", "id", "frequency"}
                        and isinstance(item, str) and item.strip()
                    ]
                    name = strings[0] if len(strings) == 1 else ""
                if name and name not in names:
                    names.append(name)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                return
            visit(parsed)

    visit(payload)
    return tuple(names)


def _history_query(item_name: str, past_names: tuple[str, ...]) -> str:
    query = " ".join(item_name.casefold().split())
    for name in past_names:  # Zepto orders these by frequency.
        candidate = " ".join(name.casefold().split())
        if query and (query in candidate or candidate in query):
            return name
    return item_name


def _notice_status(payload: Any) -> StoreStatus | None:
    text = json.dumps(payload, ensure_ascii=False).casefold()
    if any(part in text for part in (
        "unauthorized", "not authenticated", "authentication required", "auth required",
        "login required", "token expired", "needs_login", "401", "403",
    )):
        return StoreStatus.NEEDS_LOGIN
    if any(part in text for part in (
        "store closed", "store is closed", '"closed": true', '"status": "closed"',
    )):
        return StoreStatus.CLOSED
    if any(part in text for part in ("unserviceable", "not serviceable", "outside delivery")):
        return StoreStatus.UNSERVICEABLE
    if any(part in text for part in (
        "store not selected", "store context missing", "address required", "needs_address",
    )):
        return StoreStatus.NEEDS_ADDRESS
    return None


class ZeptoAdapter:
    name = "zepto"

    def __init__(
        self,
        client: ZeptoMCPClient | None = None,
        *,
        address_id: str | None = None,
        address_coordinates: tuple[float, float] | None = None,
        preview_ttl_seconds: int = 300,
    ) -> None:
        self.client = client or ZeptoMCPClient()
        self.address_id = address_id
        self.address_coordinates = address_coordinates
        self.preview_ttl_seconds = preview_ttl_seconds
        self._products: dict[str, dict[str, Any]] = {}
        self._cart: tuple[CartLine, ...] = ()
        self._snapshot: CartSnapshot | None = None
        self._past_order_names: tuple[str, ...] | None = None
        self._previews: dict[str, ZeptoPreview] = {}
        self._fee_schedule = FeeSchedule(fees_estimated=True)
        # A configured address ID is not proof the remote MCP session selected it.
        self._address_selected = False
        self._store_status = StoreStatus.NEEDS_ADDRESS

    async def resolve_items(self, items: tuple[ParsedItem, ...]) -> tuple[ItemCandidates, ...]:
        if not items:
            return ()
        # Zepto's search tool description requires this read before every search.
        # Keep the returned purchase history in memory only; never log or persist it.
        if self._past_order_names is None:
            try:
                past_orders = await self.client.call("get_past_order_items", {})
            except AuthRequired as exc:
                self._store_status = StoreStatus.NEEDS_LOGIN
                logger.warning(
                    "Zepto product lookup failed: tool=%s error_type=%s message=%s response_structure=%s",
                    exc.tool_name or "get_past_order_items", type(exc).__name__,
                    exc.safe_message, _shape(exc.response_structure),
                )
                return tuple(ItemCandidates(item_id=item.id, candidates=()) for item in items)
            except (StoreClosed, Unserviceable):
                self._store_status = StoreStatus.UNAVAILABLE
                return tuple(ItemCandidates(item_id=item.id, candidates=()) for item in items)
            except AddressRequired:
                self._store_status = StoreStatus.NEEDS_ADDRESS
                return tuple(ItemCandidates(item_id=item.id, candidates=()) for item in items)
            except ToolError as exc:
                self._store_status = StoreStatus.UNAVAILABLE
                logger.warning(
                    "Zepto product lookup prerequisite failed: tool=%s error_type=%s message=%s response_structure=%s",
                    exc.tool_name or "get_past_order_items", type(exc).__name__,
                    exc.safe_message, _shape(exc.response_structure),
                )
                return tuple(ItemCandidates(item_id=item.id, candidates=()) for item in items)
            history_notice = _notice_status(past_orders)
            if history_notice is not None:
                self._store_status = history_notice
                if history_notice is StoreStatus.NEEDS_LOGIN:
                    logger.warning(
                        "Zepto prerequisite response requires authentication: tool=get_past_order_items "
                        "error_type=AuthenticationRequired message=%s response_structure=%s",
                        "Zepto rejected purchase-history lookup as unauthenticated; raw response is withheld.",
                        _shape(past_orders),
                    )
                return tuple(ItemCandidates(item_id=item.id, candidates=()) for item in items)
            self._past_order_names = _past_order_product_names(past_orders)
        past_names = self._past_order_names
        query_by_item = {item.id: _history_query(item.name, past_names) for item in items}

        # Follow Zepto's tool contract: single requests use search_products; lists use
        # search_multiple_products. Both tool descriptions require past-order lookup first.
        search_tool = "search_products" if len(items) == 1 else "search_multiple_products"
        try:
            if len(items) == 1:
                payload = await self.client.call(search_tool, {"query": query_by_item[items[0].id]})
            else:
                payload = await self.client.call(
                    search_tool, {"queries": [query_by_item[item.id] for item in items]},
                )
        except AuthRequired as exc:
            self._store_status = StoreStatus.NEEDS_LOGIN
            logger.warning(
                "Zepto search failed: tool=%s error_type=%s message=%s response_structure=%s",
                exc.tool_name or search_tool, type(exc).__name__, exc.safe_message,
                _shape(exc.response_structure),
            )
            return tuple(ItemCandidates(item_id=item.id, candidates=()) for item in items)
        except (StoreClosed, Unserviceable) as exc:
            self._store_status = (
                StoreStatus.CLOSED if isinstance(exc, StoreClosed) else StoreStatus.UNSERVICEABLE
            )
            return tuple(ItemCandidates(item_id=item.id, candidates=()) for item in items)
        except AddressRequired:
            self._store_status = StoreStatus.NEEDS_ADDRESS
            return tuple(ItemCandidates(item_id=item.id, candidates=()) for item in items)
        except (ToolError, Timeout) as exc:
            self._store_status = StoreStatus.UNAVAILABLE
            logger.warning(
                "Zepto search failed: tool=%s error_type=%s message=%s response_structure=%s",
                exc.tool_name or search_tool, type(exc).__name__, exc.safe_message,
                _shape(exc.response_structure),
            )
            return tuple(ItemCandidates(item_id=item.id, candidates=()) for item in items)
        notice = _notice_status(payload)
        if notice is not None:
            self._store_status = notice
            if notice is StoreStatus.NEEDS_LOGIN:
                logger.warning(
                    "Zepto search response requires authentication: tool=%s "
                    "error_type=AuthenticationRequired message=%s response_structure=%s",
                    search_tool,
                    "Zepto rejected product search as unauthenticated; raw response is withheld.",
                    _shape(payload),
                )
            if notice is not StoreStatus.OK:
                return tuple(ItemCandidates(item_id=item.id, candidates=()) for item in items)
        if len(items) == 1:
            data = _to_dict(payload)
            products = data.get("products")
            products_by_section = (
                [(query_by_item[items[0].id], _to_dict(product)) for product in products]
                if isinstance(products, list) else None
            )
        else:
            products_by_section = _products_by_section(payload)
        if products_by_section is None:
            self._store_status = StoreStatus.UNAVAILABLE
            logger.warning("Zepto search_multiple_products returned an unexpected response structure.")
            return tuple(ItemCandidates(item_id=item.id, candidates=()) for item in items)
        by_query: dict[str, list[dict[str, Any]]] = {}
        for query, product in products_by_section:
            by_query.setdefault(query.casefold(), []).append(product)

        resolved: list[ItemCandidates] = []
        for item in items:
            products = by_query.get(query_by_item[item.id].casefold(), [])
            candidates: list[Candidate] = []
            for product in products:
                price = _product_price_rupees(product)
                # update_cart requires both documented cart identifiers. Catalog IDs or
                # variantId cannot substitute for productVariantId in that schema.
                variant_id = product.get("productVariantId")
                store_product_id = product.get("storeProductId")
                title = product.get("name")
                if (
                    not isinstance(variant_id, str) or not variant_id
                    or not isinstance(store_product_id, str) or not store_product_id
                    or not isinstance(title, str) or not title.strip()
                    or price is None
                ):
                    continue
                available = product.get("availableQuantity")
                if available is not None and (
                    not isinstance(available, (int, float)) or isinstance(available, bool)
                ):
                    continue
                max_qty = int(available) if available is not None else None
                # Null/missing availability is unknown, not proof of available stock.
                in_stock = max_qty is not None and max_qty > 0
                candidate = Candidate(
                    sku_id=variant_id,
                    title=title.strip(),
                    brand=None,  # Search schema does not expose brand; no title inference.
                    pack_size=_pack_size(product),
                    price=price,
                    mrp=(float(Decimal(str(product["mrp"])) / 100)
                         if isinstance(product.get("mrp"), (int, float)) and not isinstance(product.get("mrp"), bool) else None),
                    in_stock=in_stock,
                    max_qty=max_qty,
                    eta_min=None,  # No ETA field exists in the captured search schema.
                )
                candidates.append(candidate)
                self._products[variant_id] = product
            resolved.append(ItemCandidates(item_id=item.id, candidates=tuple(candidates)))
        return tuple(resolved)

    async def add_to_cart(self, lines: tuple[CartLine, ...]) -> CartSnapshot:
        if not lines:
            raise ToolError("Cannot build an empty Zepto cart.")
        cart_items: list[dict[str, Any]] = []
        for line in lines:
            product = self._products.get(line.sku_id)
            if product is None:
                raise ToolError("A selected Zepto product has no verified cart identifiers.")
            variant_id = product.get("productVariantId") or product.get("variantId")
            store_product_id = product.get("storeProductId")
            if not isinstance(variant_id, str) or not isinstance(store_product_id, str):
                raise ToolError("Zepto search response omitted required cart identifiers.")
            cart_items.append({
                "productVariantId": variant_id,
                "storeProductId": store_product_id,
                "quantity": line.qty,
                "name": product.get("name", line.title),
                "label": product.get("name", line.title),
                "price": product.get("price"),
                "mrp": product.get("mrp"),
                "packSize": product.get("packSize"),
                "availableQuantity": product.get("availableQuantity"),
                "isAd": bool(product.get("isAd", False)),
                "variantId": product.get("variantId"),
            })
        # deviceId and replaceCart are required by the recorded input schema.
        try:
            await self.client.call("update_cart", {
                "deviceId": "basketpilot-local",
                "cartItems": cart_items,
                # Preserve unrelated items already present in the customer's cart.
                "replaceCart": False,
            })
        except Exception as exc:
            # A transport failure can happen after the server applied the update.
            # Read back once for diagnosis; never issue compensating/removal writes.
            try:
                await self.client.call("view_cart", {})
            except Exception:
                pass
            raise ToolError(
                "Zepto cart update failed; the update may have partially applied. "
                "Check the current cart before retrying.",
                tool_name="update_cart",
                response_structure={"readback_attempted": True},
            ) from None
        payload = await self.client.call("view_cart", {})
        snapshot = self._snapshot_from_cart(payload, lines)
        self._cart = lines
        self._snapshot = snapshot
        return snapshot

    def _snapshot_from_cart(
        self, payload: Any, intent: tuple[CartLine, ...],
    ) -> CartSnapshot:
        # The tool description guarantees IDs and quantities, but its captured schema has no
        # output shape or bill fields. Validate only fields actually documented there.
        observed: list[tuple[str, int]] = []
        rows = payload if isinstance(payload, list) else None
        if rows is None:
            # Avoid guessing an undocumented response container key.
            rows = []
        for row in rows:
            record = _to_dict(row)
            product_id = record.get("productVariantId")
            quantity = record.get("quantity")
            if isinstance(product_id, str) and isinstance(quantity, (int, float)):
                observed.append((product_id, int(quantity)))

        intended = []
        for line in intent:
            product = self._products.get(line.sku_id, {})
            intended.append((product.get("productVariantId") or product.get("variantId"), line.qty))
        errors: list[str] = []
        if not observed:
            errors.append("Zepto cart response has no documented top-level item list; cart could not be validated.")
        elif sorted(observed) != sorted(intended):
            errors.append("Zepto cart items or quantities differ from the requested cart.")
        errors.append("Zepto view_cart response does not expose item prices; candidate prices are not verified.")

        # Output schema does not expose a bill amount or fee lines. Expected item subtotal
        # is diagnostic only and cannot be represented as a verified bill.
        subtotal = float(sum((Decimal(str(line.unit_price)) * line.qty for line in intent), Decimal(0)))
        return CartSnapshot(
            platform=self.name,
            lines=intent,
            subtotal=subtotal,
            fees=self._fee_schedule,
            total=subtotal,
            validation_errors=tuple(errors + [
                "Zepto view_cart schema does not expose a bill total; cart total could not be verified.",
                "Zepto view_cart schema does not expose bill totals or itemized fees; fees are estimated.",
            ]),
        )

    def _live_cart_matches_intent(self, payload: Any, intent: tuple[CartLine, ...]) -> bool:
        if not isinstance(payload, list):
            return False
        observed = []
        for row in payload:
            record = _to_dict(row)
            product_id, quantity = record.get("productVariantId"), record.get("quantity")
            if not isinstance(product_id, str) or not isinstance(quantity, (int, float)):
                return False
            observed.append((product_id, int(quantity)))
        expected = []
        for line in intent:
            product = self._products.get(line.sku_id, {})
            product_id = product.get("productVariantId") or product.get("variantId")
            if not isinstance(product_id, str):
                return False
            expected.append((product_id, line.qty))
        return sorted(observed) == sorted(expected)

    async def get_fee_schedule(self) -> FeeSchedule:
        return self._fee_schedule

    async def get_store_status(self) -> StoreStatus:
        if self._store_status in {
            StoreStatus.NEEDS_LOGIN, StoreStatus.CLOSED,
            StoreStatus.UNSERVICEABLE, StoreStatus.UNAVAILABLE,
        }:
            return self._store_status
        if not self._address_selected:
            self._store_status = StoreStatus.NEEDS_ADDRESS
            return self._store_status
        if self.address_coordinates is None:
            if self.address_id is None:
                self._store_status = StoreStatus.NEEDS_ADDRESS
            return self._store_status
        try:
            response = await self.client.call("get_location_serviceability", {
                "latitude": self.address_coordinates[0],
                "longitude": self.address_coordinates[1],
            })
        except AuthRequired:
            self._store_status = StoreStatus.NEEDS_LOGIN
        except StoreClosed:
            self._store_status = StoreStatus.CLOSED
        except Unserviceable:
            self._store_status = StoreStatus.UNSERVICEABLE
        except AddressRequired:
            self._store_status = StoreStatus.NEEDS_ADDRESS
        except ToolError:
            self._store_status = StoreStatus.UNAVAILABLE
        else:
            text = json.dumps(response, ensure_ascii=False).casefold()
            notice = _notice_status(response)
            if notice is not None:
                self._store_status = notice
            elif "needs_address" in text or "address required" in text:
                self._store_status = StoreStatus.NEEDS_ADDRESS
            elif "closed" in text:
                self._store_status = StoreStatus.CLOSED
            elif "unserviceable" in text or "not serviceable" in text:
                self._store_status = StoreStatus.UNSERVICEABLE
            elif "serviceable" in text and "false" in text:
                self._store_status = StoreStatus.UNSERVICEABLE
            elif "serviceable" in text and "true" in text:
                self._store_status = StoreStatus.OK
            else:
                self._store_status = StoreStatus.UNAVAILABLE
        return self._store_status

    async def select_saved_address(self, address_id: str) -> StoreStatus:
        try:
            response = await self.client.call("select_saved_address", {"addressId": address_id})
        except AuthRequired:
            self._store_status = StoreStatus.NEEDS_LOGIN
        except (Unserviceable, StoreClosed):
            self._store_status = StoreStatus.UNSERVICEABLE
        except AddressRequired:
            self._store_status = StoreStatus.NEEDS_ADDRESS
        except ToolError:
            self._store_status = StoreStatus.UNAVAILABLE
        else:
            notice = _notice_status(response)
            if isinstance(response, str) and response.casefold().startswith("error:"):
                self._store_status = notice or StoreStatus.UNAVAILABLE
            else:
                self.address_id = address_id
                self._address_selected = True
                self._store_status = notice or StoreStatus.OK
        return self._store_status

    async def preview_order(self) -> ZeptoPreview:
        if self._snapshot is None or self._snapshot.validation_errors:
            raise ToolError("Cannot preview: Zepto cart has not been verified with an exposed bill.")
        if not self.address_id:
            raise ToolError("Cannot preview without a selected address.")
        bill_paise = rupees_to_paise(self._snapshot.total)
        digest = self._cart_hash(self._cart, bill_paise, self.address_id)
        token = secrets.token_urlsafe(32)
        preview = ZeptoPreview(
            platform=self.name,
            lines=self._cart,
            total_paise=bill_paise,
            address_id=self.address_id,
            preview_token=token,
            expires_at=time.monotonic() + self.preview_ttl_seconds,
            cart_hash=digest,
        )
        self._previews[token] = preview
        return preview

    @staticmethod
    def _cart_hash(lines: tuple[CartLine, ...], bill_paise: int, address_id: str) -> str:
        data = {
            "platform": "zepto",
            "items": sorted((line.sku_id, line.qty) for line in lines),
            "bill_paise": bill_paise,
            "address_id": address_id,
        }
        return hashlib.sha256(json.dumps(data, separators=(",", ":")).encode()).hexdigest()

    async def place_order(
        self,
        preview_token: str,
        *,
        confirmed: bool,
        payment: str = "cod",
    ) -> Any:
        if not confirmed:
            raise ToolError("Explicit confirmation is required before placing an order.")
        if os.getenv("BP_ALLOW_ORDERS", "false").casefold() != "true":
            raise ToolError("Zepto order placement is disabled (BP_ALLOW_ORDERS is not true).")
        preview = self._previews.get(preview_token)
        if preview is None or preview.platform != self.name or time.monotonic() >= preview.expires_at:
            raise ToolError("Preview token is invalid, expired, or belongs to another platform.")
        if self.address_id != preview.address_id or self._snapshot is None:
            raise ToolError("Cart or address changed after the preview; create a new preview.")
        live_cart = await self.client.call("view_cart", {})
        if not self._live_cart_matches_intent(live_cart, self._cart):
            raise ToolError("Live Zepto cart changed after the preview; create a new preview.")
        current_hash = self._cart_hash(
            self._cart, rupees_to_paise(self._snapshot.total), self.address_id,
        )
        if current_hash != preview.cart_hash:
            raise ToolError("Cart changed after the preview; create a new preview.")
        tool_for_payment = {
            "cod": "create_order",
            "online": "create_online_payment_order",
            "wallet": "create_wallet_order",
        }.get(payment)
        if tool_for_payment is None:
            raise ToolError("Unsupported Zepto payment choice.")
        methods = await self.client.call("get_payment_methods", {})
        if isinstance(methods, str) and methods.casefold().startswith("error:"):
            raise ToolError("Zepto could not verify payment methods for the selected cart.")
        return await self.client.call_money(tool_for_payment, {
            "confirmOrder": True,
            "userAddressId": preview.address_id,
        }, authorized=True)
