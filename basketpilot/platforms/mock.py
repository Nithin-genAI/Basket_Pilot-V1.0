"""Deterministic grocery fixtures for development and zero-credential demos."""

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


def _offer(
    platform_sku: str,
    title: str,
    brand: str | None,
    pack_size: str,
    price: float,
    keywords: tuple[str, ...],
    *,
    in_stock: bool = True,
    max_qty: int | None = None,
) -> MockOffer:
    return MockOffer(
        keywords,
        Candidate(
            sku_id=platform_sku,
            title=title,
            brand=brand,
            pack_size=pack_size,
            price=price,
            in_stock=in_stock,
            max_qty=max_qty,
            eta_min=18,
            match_score=0.95,
        ),
    )


class MockAdapter:
    """In-memory platform substitute; placement is never represented as live."""

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
            query = " ".join(item.name.casefold().split())
            found = tuple(
                offer.candidate
                for offer in self._offers
                if any(
                    keyword.casefold() in query or query in keyword.casefold()
                    for keyword in offer.keywords
                )
            )
            results.append(ItemCandidates(item_id=item.id, candidates=found))
        return tuple(results)

    async def add_to_cart(self, lines: tuple[CartLine, ...]) -> CartSnapshot:
        self._cart = lines
        subtotal = round(sum(line.qty * line.unit_price for line in lines), 2)
        fees = self._fees
        delivery = fees.delivery or 0
        if fees.free_delivery_threshold is not None and subtotal >= fees.free_delivery_threshold:
            delivery = 0
        handling = fees.handling or 0
        small_cart = fees.small_cart or 0
        late_night = fees.late_night or 0
        other = fees.other or 0
        if fees.small_cart_threshold is not None and subtotal >= fees.small_cart_threshold:
            small_cart = 0
        total = round(subtotal + delivery + handling + small_cart + late_night + other, 2)
        effective_fees = FeeSchedule(
            delivery=delivery,
            handling=handling,
            small_cart=small_cart,
            late_night=late_night,
            other=other,
            free_delivery_threshold=fees.free_delivery_threshold,
            min_order=fees.min_order,
            small_cart_threshold=fees.small_cart_threshold,
            fees_estimated=fees.fees_estimated,
        )
        return CartSnapshot(
            platform=self.name,
            lines=lines,
            subtotal=subtotal,
            fees=effective_fees,
            total=total,
        )

    async def get_fee_schedule(self) -> FeeSchedule:
        return self._fees

    async def get_store_status(self) -> StoreStatus:
        return self._store_status


def default_mock_adapters() -> tuple[MockAdapter, MockAdapter]:
    """Return two catalog fixtures with 40+ SKUs and deliberate edge cases."""
    zepto_offers = (
        _offer("z-milk-1l", "Toned Milk 1 L", "Nandini", "1 L", 62, ("milk", "doodh")),
        _offer("z-milk-500", "Toned Milk 500 ml", "Nandini", "500 ml", 32, ("milk", "doodh")),
        _offer("z-milk-6x200", "Toned Milk 6 x 200 ml", "Nandini", "6 x 200 ml", 68, ("milk", "doodh")),
        _offer("z-tomato-500", "Tomato 500 g", None, "500 g", 25, ("tamatar", "tamato", "tomato", "tomatos")),
        _offer("z-potato-1kg", "Potato 1 kg", None, "1 kg", 30, ("aloo", "potato")),
        _offer("z-eggs-12", "Country Eggs 12 pcs", None, "12 piece", 96, ("anday", "anda", "egg", "eggs")),
        _offer("z-atta-1kg", "Whole Wheat Atta 1 kg", "Aashirvaad", "1 kg", 48, ("atta", "aata", "flour")),
        _offer("z-atta-500", "Whole Wheat Atta 500 g", "Aashirvaad", "500 g", 26, ("atta", "aata", "flour")),
        _offer("z-onion-1kg", "Onion 1 kg", None, "1 kg", 36, ("pyaaz", "onion", "onions")),
        _offer("z-rice-1kg", "Sona Masoori Rice 1 kg", None, "1 kg", 75, ("chawal", "rice")),
        _offer("z-sugar-1kg", "Sugar 1 kg", None, "1 kg", 50, ("cheeni", "sugar")),
        _offer("z-tea-250", "Assam Tea 250 g", "Tata Tea", "250 g", 130, ("chai patti", "tea", "chai")),
        _offer("z-salt-1kg", "Iodised Salt 1 kg", "Tata Salt", "1 kg", 22, ("namak", "salt")),
        _offer("z-banana-6", "Robusta Banana 6 pcs", None, "6 piece", 40, ("kela",)),
        _offer("z-apple-1kg", "Apple 1 kg", None, "1 kg", 160, ("seb", "apple", "aple")),
        _offer("z-paneer-amul-200", "Amul Fresh Paneer 200 g", "Amul", "200 g", 90, ("amul paneer",)),
        _offer("z-okra-500", "Bhindi 500 g", None, "500 g", 30, ("bhindi", "okra")),
        _offer("z-spinach-500", "Palak 500 g", None, "500 g", 24, ("palak", "spinach")),
        _offer("z-moong-1kg", "Moong Dal 1 kg", None, "1 kg", 110, ("moong dal", "moong")),
        _offer("z-mustard-1l", "Kachi Ghani Mustard Oil 1 L", "Fortune", "1 L", 160, ("sarson tel", "mustard oil")),
        _offer("z-turmeric-100", "Haldi Powder 100 g", None, "100 g", 30, ("haldi", "turmeric")),
        _offer("z-coconut-200", "Coconut Water 200 ml", None, "200 ml", 45, ("nariyal pani", "coconut water"), in_stock=False),
    )

    instamart_offers = (
        _offer("i-milk-1l", "Toned Milk 1 L", "Nandini", "1 L", 58, ("milk", "doodh")),
        _offer("i-milk-500", "Toned Milk 500 ml", "Nandini", "500 ml", 31, ("milk", "doodh")),
        _offer("i-milk-6x200", "Toned Milk 6 x 200 ml", "Nandini", "6 x 200 ml", 65, ("milk", "doodh")),
        _offer("i-tomato-500", "Tomato 500 g", None, "500 g", 27, ("tamatar", "tamato", "tomato", "tomatos")),
        _offer("i-potato-1kg", "Potato 1 kg", None, "1 kg", 28, ("aloo", "potato")),
        _offer("i-eggs-12", "Farm Eggs 12 pcs", None, "12 piece", 89, ("anday", "anda", "egg", "eggs")),
        _offer("i-atta-1kg", "Whole Wheat Atta 1 kg", "Aashirvaad", "1 kg", 42, ("atta", "aata", "flour")),
        _offer("i-atta-500", "Whole Wheat Atta 500 g", "Aashirvaad", "500 g", 25, ("atta", "aata", "flour")),
        _offer("i-onion-1kg", "Onion 1 kg", None, "1 kg", 34, ("pyaaz", "onion", "onions")),
        _offer("i-dahi-1l", "Fresh Dahi 1 L", None, "1 L", 78, ("dahi", "curd")),
        _offer("i-rice-1kg", "Sona Masoori Rice 1 kg", None, "1 kg", 70, ("chawal", "rice")),
        _offer("i-sugar-1kg", "Sugar 1 kg", None, "1 kg", 43, ("cheeni", "sugar")),
        _offer("i-tea-250", "Assam Tea 250 g", "Tata Tea", "250 g", 125, ("chai patti", "tea", "chai")),
        _offer("i-salt-1kg", "Iodised Salt 1 kg", "Tata Salt", "1 kg", 20, ("namak", "salt")),
        _offer("i-banana-6", "Robusta Banana 6 pcs", None, "6 piece", 38, ("kela",)),
        _offer("i-apple-1kg", "Apple 1 kg", None, "1 kg", 155, ("seb", "apple", "aple")),
        _offer("i-paneer-local-200", "Local Paneer 200 g", "Local", "200 g", 80, ("local paneer",)),
        _offer("i-okra-500", "Bhindi 500 g", None, "500 g", 28, ("bhindi", "okra")),
        _offer("i-moong-1kg", "Moong Dal 1 kg", None, "1 kg", 106, ("moong dal", "moong")),
        _offer("i-mustard-1l", "Kachi Ghani Mustard Oil 1 L", "Fortune", "1 L", 155, ("sarson tel", "mustard oil")),
        _offer("i-turmeric-100", "Haldi Powder 100 g", None, "100 g", 28, ("haldi", "turmeric")),
        _offer("i-coconut-200", "Coconut Water 200 ml", None, "200 ml", 42, ("nariyal pani", "coconut water"), in_stock=False),
        _offer("i-basmati-1kg", "Basmati Rice 1 kg", None, "1 kg", 105, ("basmati",)),
    )

    return (
        MockAdapter(
            "zepto-mock",
            zepto_offers,
            FeeSchedule(
                delivery=1, handling=5, small_cart=0, late_night=0, other=0,
                free_delivery_threshold=193, min_order=99,
            ),
        ),
        MockAdapter(
            "instamart-mock",
            instamart_offers,
            FeeSchedule(
                delivery=20, handling=7, small_cart=0, late_night=0, other=0,
                free_delivery_threshold=149, min_order=99,
            ),
        ),
    )

