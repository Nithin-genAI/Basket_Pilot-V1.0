"""One search worker per platform adapter."""

from __future__ import annotations

from uagents import Agent, Context

from basketpilot import messages
from basketpilot.agents.wire import item_from_wire, result_to_wire
from basketpilot.models import StoreStatus
from basketpilot.platforms.base import PlatformAdapter


def create_search_agent(adapter: PlatformAdapter, seed: str, port: int) -> Agent:
    agent = Agent(name=f"bp-search-{adapter.name}", seed=seed, port=port, network="testnet")

    @agent.on_message(model=messages.SearchRequest)
    async def search_request(ctx: Context, sender: str, msg: messages.SearchRequest) -> None:
        items = tuple(item_from_wire(row) for row in msg.items)
        ctx.logger.info("Search worker %s resolving %d items for %s", adapter.name, len(items), msg.session_id)
        try:
            found = await adapter.resolve_items(items)
            status = await adapter.get_store_status()
            fees = await adapter.get_fee_schedule()
            notice = None
        except Exception as exc:  # Adapter failures become unavailable results, never a traceback in chat.
            ctx.logger.warning("Search worker %s failed (%s)", adapter.name, type(exc).__name__)
            found = tuple()
            status = StoreStatus.UNAVAILABLE
            fees = None
            notice = "Platform search is temporarily unavailable."
        await ctx.send(sender, result_to_wire(msg.session_id, adapter.name, found, status, notice, fees))

    return agent
