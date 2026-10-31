"""Adapter boundary. The orchestrator depends on this protocol, not MCP details."""

from __future__ import annotations

from typing import Protocol

from basketpilot.models import (
    CartLine, CartSnapshot, FeeSchedule, ItemCandidates, ParsedItem, StoreStatus,
)


class PlatformAdapter(Protocol):
    name: str

    async def resolve_items(self, items: tuple[ParsedItem, ...]) -> tuple[ItemCandidates, ...]: ...

    async def add_to_cart(self, lines: tuple[CartLine, ...]) -> CartSnapshot: ...

    async def get_fee_schedule(self) -> FeeSchedule: ...

    async def get_store_status(self) -> StoreStatus: ...

