"""Deterministic fixture adapter for development and zero-credential demos."""

from __future__ import annotations

from dataclasses import dataclass

from basketpilot.models import (
    Candidate, CartLine, CartSnapshot, FeeSchedule, ItemCandidates, ParsedItem,
    StoreStatus,
)


@dataclass(frozen=True, slots=True)
class MockOffer:
    keywords: tuple[str, ...]
    candidate: Candidate


class MockAdapter:
    """Small in-memory platform substitute; never represents a live order."""

    def __init__(
        self,
        name: str,
        offers: tuple[MockOffer, ...],
        fees: FeeSchedule,
        *,
        store_status: StoreStatus = StoreStatus.OK,
    ) -> None:
        self.name = name
        self._offers = offers
        self._fees = fees
        self._store_status = store_status
        self._cart: tuple[CartLine, ...] = ()

    async def resolve_items(self, items: tuple[ParsedItem, ...]) -> tuple[ItemCandidates, ...]:
        results: list[ItemCandidates] = []
        for item in items:
            query = item.name.casefold()
            found = tuple(
                offer.candidate
                for offer in self._offers
                if any(keyword.casefold() in query or query in keyword.casefold() for keyword in offer.keywords)
            )
            results.append(ItemCandidates(item_id=item.id, candidates=found))
        return tuple(results)

    async def add_to_cart(self, lines: tuple[CartLine, ...]) -> CartSnapshot:
        self._cart = lines
        subtotal = round(sum(line.qty * line.unit_price for line in lines), 2)
        fees = self._fees
        delivery = fees.delivery
        if fees.free_delivery_threshold is not None and subtotal >= fees.free_delivery_threshold:
            delivery = 0
        total = round(subtotal + delivery + fees.handling + fees.small_cart + fees.late_night + fees.other, 2)
        effective_fees = FeeSchedule(
            delivery=delivery,
            handling=fees.handling,
            small_cart=fees.small_cart,
            late_night=fees.late_night,
            other=fees.other,
            free_delivery_threshold=fees.free_delivery_threshold,
            min_order=fees.min_order,
        )
        return CartSnapshot(platform=self.name, lines=lines, subtotal=subtotal, fees=effective_fees, total=total)

    async def get_fee_schedule(self) -> FeeSchedule:
        return self._fees

    async def get_store_status(self) -> StoreStatus:
        return self._store_status


def default_mock_adapters() -> tuple[MockAdapter, MockAdapter]:
    """Offers are illustrative fixtures and must be labeled as mock data in UX."""
    zepto = MockAdapter(
        "zepto-mock",
        offers=(
            MockOffer(("milk",), Candidate("z-milk-1l", "Toned Milk 1 L", "Mock Dairy", "1 L", 62, unit_price=62)),
            MockOffer(("onion",), Candidate("z-onion-1kg", "Onion 1 kg", None, "1 kg", 38, unit_price=38)),
            MockOffer(("eggs", "egg"), Candidate("z-eggs-12", "Eggs 12 pcs", None, "12 piece", 96, unit_price=8)),
        ),
        fees=FeeSchedule(delivery=25, handling=5, free_delivery_threshold=199, min_order=99),
    )
    instamart = MockAdapter(
        "instamart-mock",
        offers=(
            MockOffer(("milk",), Candidate("i-milk-1l", "Toned Milk 1 L", "Mock Dairy", "1 L", 59, unit_price=59)),
            MockOffer(("onion",), Candidate("i-onion-1kg", "Onion 1 kg", None, "1 kg", 42, unit_price=42)),
            MockOffer(("eggs", "egg"), Candidate("i-eggs-12", "Eggs 12 pcs", None, "12 piece", 91, unit_price=7.58)),
        ),
        fees=FeeSchedule(delivery=20, handling=7, free_delivery_threshold=149, min_order=99),
    )
    return zepto, instamart

