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

from basketpilot.platforms.zepto_client import ZeptoError, ZeptoMCPClient, _shape, redact


RAW_DIR = ROOT / "docs" / "raw"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "zepto"
PINCODE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")
ADDRESS_ID = re.compile(r"(?i)\bID\s*:\s*[\"']?([a-f0-9-]{16,})")
ADDRESS_LABEL = re.compile(r"(?i)\bAddress\s*:\s*([^,\n]+?)\s*,\s*ID\s*:")
SAFE_ADDRESS_LABELS = {
    "home", "home address", "work", "work address", "office", "office address", "other",
}


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


def _walk_text(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_text(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_text(child)


def _decoded(value: Any) -> Any:
    """Decode JSON text wrappers used by MCP clients, without assuming the root shape."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return value
        return _decoded(parsed)
    if isinstance(value, dict):
        return {key: _decoded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decoded(item) for item in value]
    return value


def _safe_address_label(value: Any) -> str:
    """Only display common address labels; custom names may contain personal data."""
    if not isinstance(value, str):
        return "saved address"
    label = " ".join(value.split()).strip()
    return label if label.casefold() in SAFE_ADDRESS_LABELS else "saved address"


def _saved_address_summaries(value: Any) -> list[tuple[str, str]]:
    """Return local-display summaries from nested JSON or the captured text response form."""
    decoded = _decoded(_plain(value))
    summaries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in _walk(decoded):
        normalized = {re.sub(r"[^a-z0-9]", "", str(key).casefold()): item for key, item in entry.items()}
        address_id = normalized.get("addressid")
        if not isinstance(address_id, str) or not address_id.strip():
            continue
        label_value = next((normalized[key] for key in (
            "label", "addresslabel", "addresstype", "tag", "name",
        ) if key in normalized), None)
        clean_id = address_id.strip()
        if clean_id not in seen:
            summaries.append((_safe_address_label(label_value), clean_id))
            seen.add(clean_id)

    # Older MCP responses have been captured as text like "Address: Home, ID: ...".
    for node in _walk_text(decoded):
        id_match = ADDRESS_ID.search(node)
        if id_match and id_match.group(1) not in seen:
            label_match = ADDRESS_LABEL.search(node)
            label = _safe_address_label(label_match.group(1)) if label_match else "saved address"
            summaries.append((label, id_match.group(1)))
            seen.add(id_match.group(1))
    return summaries


def _extract_address_id(value: Any) -> str | None:
    """Use the documented addressId field, or the captured legacy `ID:` text form."""
    for entry in _walk(value):
        address_id = entry.get("addressId")
        if isinstance(address_id, str) and address_id.strip():
            return address_id.strip()
    if isinstance(value, str):
        match = ADDRESS_ID.search(value)
        if match:
            return match.group(1)
    return None


async def _read(
    client: ZeptoMCPClient, tool: str, args: dict[str, Any], outputs: dict[str, Any],
    *, output_key: str | None = None,
) -> Any:
    result = await client.call(tool, args)
    outputs[output_key or tool] = result
    print(f"READ OK tool={tool} response_structure={json.dumps(_shape(result), sort_keys=True)}", flush=True)
    return result


async def _confirmed_write(
    tool: str, description: str, args: dict[str, Any], client: ZeptoMCPClient,
) -> tuple[bool, Any]:
    print(f"About to call WRITE tool {tool}: {description}")
    answer = input("Type y to continue: ").strip()
    if answer != "y":
        print(f"Skipped {tool}; no write was sent.")
        return False, None
    return True, await client.call(tool, args)


async def record(
    *, include_cart_write: bool, select_saved_address: bool, save_responses: bool = True,
) -> None:
    outputs: dict[str, Any] = {}
    async with ZeptoMCPClient() as client:
        addresses = await _read(client, "list_saved_addresses", {}, outputs)
        address_summaries = _saved_address_summaries(addresses)
        address_id = address_summaries[0][1] if address_summaries else _extract_address_id(addresses)
        if address_summaries:
            print("Saved addresses (IDs are shown only in this local terminal):", flush=True)
            for label, saved_id in address_summaries:
                print(f"  label={label}; address_id={saved_id}", flush=True)
        else:
            print("No saved-address ID and label pair could be extracted from the response.", flush=True)
        print(
            "Address inspection: "
            f"saved-address-response={'present' if addresses else 'empty'}; "
            f"identifier={'available' if address_id else 'not exposed'}; "
            "address/store selection was not performed.",
            flush=True,
        )
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
            await _read(client, "get_location_serviceability", {"latitude": lat, "longitude": lon}, outputs)

        if select_saved_address:
            if address_id is None:
                print("No saved address identifier was exposed; skipped address selection.")
            else:
                _selected, _selection_response = await _confirmed_write(
                    "select_saved_address", "select a saved address for this Zepto session (identifier hidden)",
                    {"addressId": address_id}, client,
                )

        multi = await _read(
            client, "search_multiple_products", {"queries": ["milk", "atta", "eggs"]}, outputs,
        )
        products = await _read(client, "search_products", {"query": "milk"}, outputs)
        variant_id = next((
            product.get("productVariantId")
            for section in _walk(multi)
            for product in [section]
            if isinstance(product.get("productVariantId"), str)
        ), None)
        if variant_id is None:
            variant_id = next((
                entry.get("productVariantId")
                for entry in _walk(products)
                if isinstance(entry.get("productVariantId"), str)
            ), None)
        if variant_id:
            await _read(client, "get_product_details", {"product_variant_id": variant_id}, outputs)
        await _read(client, "view_cart", {}, outputs, output_key="view_cart_empty")
        await _read(client, "get_payment_methods", {}, outputs)

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
                    await _read(client, "view_cart", {}, outputs, output_key="view_cart_one_item")
                    await _confirmed_write(
                        "update_cart", f"remove the temporary item {product.get('name', 'from the cart')}",
                        {"deviceId": "basketpilot-fixture-recorder", "cartItems": [{
                            "productVariantId": product["productVariantId"],
                            "storeProductId": product["storeProductId"],
                            "quantity": 0,
                        }], "replaceCart": False}, client,
                    )

    if save_responses:
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        # Raw PII-bearing MCP responses are deliberately not persisted by this recorder.
        for name, payload in outputs.items():
            plain = _plain(payload)
            safe = sanitize(plain, tool=name)
            (FIXTURE_DIR / f"{name}.json").write_text(json.dumps(safe, indent=2, ensure_ascii=False) + "\n")
        print(f"Saved {len(outputs)} sanitized response(s) in tests/fixtures/zepto/.")
    else:
        print(f"Completed {len(outputs)} read operation(s); no response files were written.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-cart-write", action="store_true", help="prompt before add/remove cart writes")
    parser.add_argument("--select-saved-address", action="store_true", help="prompt before selecting an address")
    parser.add_argument("--no-save", action="store_true", help="do not write raw or sanitized MCP responses")
    args = parser.parse_args()
    try:
        asyncio.run(record(
            include_cart_write=args.include_cart_write,
            select_saved_address=args.select_saved_address,
            save_responses=not args.no_save,
        ))
    except ZeptoError as exc:
        print(f"Zepto read failed: {exc}", file=sys.stdout, flush=True)
        raise SystemExit(1) from None
    except Exception as exc:
        print(
            "Zepto recorder failed: "
            f"tool=unknown error_type={type(exc).__name__} message={redact(exc)} "
            "response_structure=unavailable",
            file=sys.stdout, flush=True,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
