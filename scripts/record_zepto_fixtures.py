"""Capture Zepto MCP responses. Reads are automatic; cart writes need typed consent."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from basketpilot.platforms.zepto_client import ZeptoError, ZeptoMCPClient


RAW_DIR = ROOT / "docs" / "raw"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "zepto"
PINCODE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")
ADDRESS_ID = re.compile(r"(?i)\bID\s*:\s*[\"']?([a-f0-9-]{16,})")


def _safe_address_text(value: Any) -> str:
    if not isinstance(value, str):
        return "[REDACTED]"
    match = PINCODE.search(value)
    return f"pincode:{match.group(1)}" if match else "[REDACTED]"


def sanitize(value: Any, *, tool: str, parent: str = "") -> Any:
    """Redact account PII while retaining product identifiers and product names."""
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            normalized = key.casefold().replace("_", "")
            if normalized in {"userid", "sessionid", "addressid", "token", "accesstoken", "refreshtoken", "authorization"}:
                clean[key] = "[REDACTED]"
            elif normalized in {"phonenumber", "contactnumber", "mobile", "phone"}:
                clean[key] = "[REDACTED]"
            elif normalized in {"fullname", "contactname", "selleraddress", "flatdetails", "buildingname", "landmark", "formattedaddress", "shortaddress"}:
                clean[key] = _safe_address_text(item) if "address" in normalized else "[REDACTED]"
            elif normalized == "name" and (tool == "list_saved_addresses" or parent == "address"):
                clean[key] = "[REDACTED]"
            else:
                clean[key] = sanitize(item, tool=tool, parent=key.casefold())
        return clean
    if isinstance(value, list):
        return [sanitize(item, tool=tool, parent=parent) for item in value]
    if isinstance(value, str):
        if tool == "list_saved_addresses":
            pincodes = sorted(set(PINCODE.findall(value)))
            return "Saved address count recorded; pincode(s): " + (
                ", ".join(pincodes) if pincodes else "not exposed"
            )
        value = PHONE.sub("[REDACTED_PHONE]", value)
    return value


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump(mode="json", by_alias=True))
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


async def _confirmed_write(
    tool: str, description: str, args: dict[str, Any], client: ZeptoMCPClient,
) -> tuple[bool, Any]:
    print(f"About to call WRITE tool {tool}: {description}")
    answer = input("Type y to continue: ").strip()
    if answer != "y":
        print(f"Skipped {tool}; no write was sent.")
        return False, None
    return True, await client.call(tool, args)


async def record(*, include_cart_write: bool, select_saved_address: bool) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Any] = {}
    async with ZeptoMCPClient() as client:
        addresses = await client.call("list_saved_addresses", {})
        outputs["list_saved_addresses"] = addresses
        locations = [
            (entry.get("latitude"), entry.get("longitude"))
            for entry in _walk(addresses)
            if isinstance(entry.get("latitude"), (int, float))
            and isinstance(entry.get("longitude"), (int, float))
        ]
        if not locations and os.getenv("ZEPTO_LATITUDE") and os.getenv("ZEPTO_LONGITUDE"):
            locations.append((float(os.environ["ZEPTO_LATITUDE"]), float(os.environ["ZEPTO_LONGITUDE"])))
        if locations:
            lat, lon = locations[0]
            outputs["get_location_serviceability"] = await client.call(
                "get_location_serviceability", {"latitude": lat, "longitude": lon},
            )

        if select_saved_address:
            address_id = next((
                entry.get("addressId") for entry in _walk(addresses)
                if isinstance(entry.get("addressId"), str)
            ), None)
            if address_id is None and isinstance(addresses, str):
                match = ADDRESS_ID.search(addresses)
                address_id = match.group(1) if match else None
            if address_id is None:
                print("No saved address identifier was exposed; skipped address selection.")
            else:
                _selected, _selection_response = await _confirmed_write(
                    "select_saved_address", "select a saved address for this Zepto session (identifier hidden)",
                    {"addressId": address_id}, client,
                )

        multi = await client.call(
            "search_multiple_products", {"queries": ["milk", "atta", "eggs"]},
        )
        outputs["search_multiple_products"] = multi
        outputs["search_products"] = await client.call("search_products", {"query": "milk"})
        variant_id = next((
            product.get("productVariantId")
            for section in _walk(multi)
            for product in [section]
            if isinstance(product.get("productVariantId"), str)
        ), None)
        products = outputs["search_products"]
        if variant_id is None:
            variant_id = next((
                entry.get("productVariantId")
                for entry in _walk(products)
                if isinstance(entry.get("productVariantId"), str)
            ), None)
        if variant_id:
            outputs["get_product_details"] = await client.call(
                "get_product_details", {"product_variant_id": variant_id},
            )
        outputs["view_cart_empty"] = await client.call("view_cart", {})
        outputs["get_payment_methods"] = await client.call("get_payment_methods", {})

        if include_cart_write:
            candidates = [
                entry for entry in _walk(products)
                if isinstance(entry.get("productVariantId"), str)
                and isinstance(entry.get("storeProductId"), str)
                and isinstance(entry.get("price"), (int, float))
            ]
            product = min(candidates, key=lambda item: item["price"]) if candidates else None
            if product is None:
                print("No search result had the documented IDs and price; skipped cart write.")
            else:
                cart_item = {
                    "productVariantId": product["productVariantId"],
                    "storeProductId": product["storeProductId"],
                    "quantity": 1,
                    "name": product.get("name"),
                    "label": product.get("name"),
                    "price": product.get("price"),
                    "mrp": product.get("mrp"),
                    "packSize": product.get("packSize"),
                    "availableQuantity": product.get("availableQuantity"),
                    "isAd": bool(product.get("isAd", False)),
                    "variantId": product.get("variantId"),
                }
                added, _ = await _confirmed_write(
                    "update_cart",
                    f"add one pack of the cheapest search result ({product.get('name', 'item')}, "
                    f"Rs {Decimal(str(product['price'])) / 100}) to the current cart",
                    {"deviceId": "basketpilot-fixture-recorder", "cartItems": [cart_item], "replaceCart": False}, client,
                )
                if added:
                    outputs["view_cart_one_item"] = await client.call("view_cart", {})
                    await _confirmed_write(
                        "update_cart", f"remove the temporary item {product.get('name', 'from the cart')}",
                        {"deviceId": "basketpilot-fixture-recorder", "cartItems": [{
                            "productVariantId": product["productVariantId"],
                            "storeProductId": product["storeProductId"],
                            "quantity": 0,
                        }], "replaceCart": False}, client,
                    )

    # The user explicitly requested raw responses in an untracked local folder.
    # Only sanitized payloads are committed as fixtures.
    for name, payload in outputs.items():
        plain = _plain(payload)
        (RAW_DIR / f"{name}.json").write_text(json.dumps(plain, indent=2, ensure_ascii=False) + "\n")
        safe = sanitize(plain, tool=name)
        (FIXTURE_DIR / f"{name}.json").write_text(json.dumps(safe, indent=2, ensure_ascii=False) + "\n")
    print(f"Saved {len(outputs)} response(s); sanitized fixtures are in tests/fixtures/zepto/.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-cart-write", action="store_true", help="prompt before add/remove cart writes")
    parser.add_argument("--select-saved-address", action="store_true", help="prompt before selecting an address")
    args = parser.parse_args()
    try:
        asyncio.run(record(
            include_cart_write=args.include_cart_write,
            select_saved_address=args.select_saved_address,
        ))
    except ZeptoError as exc:
        print(str(exc))


if __name__ == "__main__":
    main()
