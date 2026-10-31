"""Read-only Zepto search and optimizer demo. It never writes to the Zepto cart."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from basketpilot.models import SearchResult, StoreStatus
from basketpilot.optimizer import OptimizerConfig, optimize
from basketpilot.optimizer.normalize import paise_to_rupees
from basketpilot.parser import parse_list
from basketpilot.platforms.zepto import ZeptoAdapter
from basketpilot.platforms.zepto_client import ZeptoError


DEMO_LIST = """1 L milk
1 kg atta
dozen eggs
1 kg apples
500 g tomatoes"""


async def run() -> None:
    coords = None
    if os.getenv("ZEPTO_LATITUDE") and os.getenv("ZEPTO_LONGITUDE"):
        coords = (float(os.environ["ZEPTO_LATITUDE"]), float(os.environ["ZEPTO_LONGITUDE"]))
    adapter = ZeptoAdapter(address_coordinates=coords)
    items = parse_list(DEMO_LIST).items
    candidates = await adapter.resolve_items(items)
    status = await adapter.get_store_status()
    schedule = await adapter.get_fee_schedule()
    result = SearchResult(platform=adapter.name, results=candidates, store_status=status)
    plan = optimize(items, (result,), {adapter.name: schedule}, OptimizerConfig())

    print("BasketPilot — Zepto read-only demo")
    print(f"Store status: {status}")
    print("| Item | Platform | SKU | Packs | Line price |")
    print("|---|---|---|---:|---:|")
    items_by_id = {item.id: item for item in items}
    for assignment in plan.assignments:
        print(
            f"| {items_by_id[assignment.item_id].name} | {assignment.platform} "
            f"| {assignment.sku_id} | {assignment.qty} "
            f"| Rs {paise_to_rupees(assignment.line_total)} |"
        )
    print(f"\nLanded total: Rs {paise_to_rupees(plan.grand_total)}")
    print(f"Fee schedule estimated: {plan.fees_estimated}")
    if plan.unfulfilled:
        print("Unfulfilled:")
        for reason in plan.unfulfilled:
            print(f"- {reason}")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except ZeptoError as exc:
        print(str(exc))
