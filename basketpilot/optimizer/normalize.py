"""Pure offer and quantity normalization. Input prices are rupees at the boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

from basketpilot.models import Candidate, ParsedItem

_PACK_RE = re.compile(
    r"(?:(?P<count>\d+(?:\.\d+)?)\s*[x×]\s*)?"
    r"(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>kilograms?|kgs?|kg|grams?|gms?|gm|g|liters?|litres?|lts?|lt|l|ml|milliliters?|millilitres?|dozen|dz|pieces?|pcs?)\b",
    re.IGNORECASE,
)

_UNIT_SCALE: dict[str, tuple[str, Decimal, str]] = {
    "kg": ("mass", Decimal(1000), "g"),
    "kilogram": ("mass", Decimal(1000), "g"),
    "kilograms": ("mass", Decimal(1000), "g"),
    "g": ("mass", Decimal(1), "g"), "gm": ("mass", Decimal(1), "g"),
    "gram": ("mass", Decimal(1), "g"), "grams": ("mass", Decimal(1), "g"),
    "l": ("volume", Decimal(1000), "ml"), "lt": ("volume", Decimal(1000), "ml"),
    "lts": ("volume", Decimal(1000), "ml"), "liter": ("volume", Decimal(1000), "ml"),
    "liters": ("volume", Decimal(1000), "ml"), "litre": ("volume", Decimal(1000), "ml"),
    "litres": ("volume", Decimal(1000), "ml"),
    "ml": ("volume", Decimal(1), "ml"), "milliliter": ("volume", Decimal(1), "ml"),
    "milliliters": ("volume", Decimal(1), "ml"), "millilitre": ("volume", Decimal(1), "ml"),
    "millilitres": ("volume", Decimal(1), "ml"),
    "piece": ("count", Decimal(1), "piece"), "pieces": ("count", Decimal(1), "piece"),
    "pc": ("count", Decimal(1), "piece"), "pcs": ("count", Decimal(1), "piece"),
    "dozen": ("count", Decimal(12), "piece"), "dz": ("count", Decimal(12), "piece"),
}


@dataclass(frozen=True, slots=True)
class NormalizedOffer:
    platform: str
    candidate: Candidate
    packs_needed: int
    pack_quantity_base: Decimal | None
    canonical_unit: str
    unit_price_paise: int
    line_total_paise: int
    overbuy_qty: float
    overbuy_unit: str


def rupees_to_paise(amount: int | float | str | Decimal) -> int:
    """Convert an external rupee amount to integer paise exactly once."""
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def paise_to_rupees(amount: int) -> str:
    """Format integer paise for user-facing text without float arithmetic."""
    sign = "-" if amount < 0 else ""
    absolute = abs(amount)
    return f"{sign}{absolute // 100}.{absolute % 100:02d}"


def _unit_info(unit: str | None) -> tuple[str, Decimal, str] | None:
    if unit is None:
        return None
    raw = unit.strip().casefold()
    if raw in {"dozen", "dz"}:
        return _UNIT_SCALE[raw]
    normalized = raw.rstrip("s")
    if normalized in {"kg", "kilogram", "g", "gm", "gram", "l", "lt", "liter", "litre",
                      "ml", "milliliter", "millilitre", "piece", "pc"}:
        return _UNIT_SCALE[normalized]
    return None


def _pack_data(pack_size: str) -> tuple[Decimal, str] | None:
    match = _PACK_RE.search(pack_size)
    if match is None:
        return None
    unit_raw = match.group("unit").casefold()
    aliases = {
        "kgs": "kg", "kilogram": "kg", "kilograms": "kg", "grams": "g", "gram": "g",
        "gms": "g", "gm": "g", "liters": "l", "liter": "l", "litres": "l",
        "litre": "l", "lts": "l", "lt": "l", "milliliters": "ml", "milliliter": "ml",
        "millilitres": "ml", "millilitre": "ml", "dz": "dozen", "pcs": "piece",
        "pc": "piece", "pieces": "piece",
    }
    unit_raw = aliases.get(unit_raw, unit_raw)
    info = _unit_info(unit_raw)
    if info is None:
        return None
    _, scale, canonical = info
    count = Decimal(match.group("count") or "1")
    amount = Decimal(match.group("amount")) * scale * count
    return amount, canonical


def normalize_candidate(platform: str, item: ParsedItem, candidate: Candidate) -> NormalizedOffer | None:
    """Normalize one candidate; return None when stock, brand, or pack limits fail."""
    if not candidate.in_stock:
        return None
    if item.brand and (candidate.brand is None or candidate.brand.casefold() != item.brand.casefold()):
        return None

    price_paise = rupees_to_paise(candidate.price)
    pack_data = _pack_data(candidate.pack_size)
    requested_info = _unit_info(item.unit)

    if requested_info is not None and pack_data is not None:
        requested_dimension, requested_scale, _ = requested_info
        pack_amount, pack_canonical = pack_data
        pack_info = _unit_info(pack_canonical)
        if pack_info is not None and requested_dimension == pack_info[0]:
            requested_base = Decimal(str(item.qty)) * requested_scale
            packs = int((requested_base / pack_amount).to_integral_value(rounding=ROUND_CEILING))
            overbuy_base = Decimal(packs) * pack_amount - requested_base
            overbuy = float(overbuy_base / requested_scale)
            divisor = Decimal(100) if requested_dimension == "mass" else (
                Decimal(1000) if requested_dimension == "volume" else Decimal(1)
            )
            canonical = {
                "mass": "100 g",
                "volume": "L",
                "count": "piece",
            }[requested_dimension]
            unit_price = int(
                (Decimal(price_paise) / (pack_amount / divisor)).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
            return _make_offer(
                platform, candidate, packs, pack_amount, canonical, unit_price,
                price_paise, overbuy, item.unit or canonical,
            )

    # Unknown/incompatible units: interpret requested quantity as pack count.
    packs = int(Decimal(str(item.qty)).to_integral_value(rounding=ROUND_CEILING))
    if packs <= 0:
        return None
    return _make_offer(
        platform, candidate, packs, None, "pack", price_paise, price_paise,
        float(Decimal(packs) - Decimal(str(item.qty))), "pack",
    )


def _make_offer(
    platform: str,
    candidate: Candidate,
    packs: int,
    pack_amount: Decimal | None,
    canonical: str,
    unit_price: int,
    one_pack_price_paise: int,
    overbuy: float,
    overbuy_unit: str,
) -> NormalizedOffer | None:
    if packs < 1 or (candidate.max_qty is not None and packs > candidate.max_qty):
        return None
    return NormalizedOffer(
        platform=platform,
        candidate=candidate,
        packs_needed=packs,
        pack_quantity_base=pack_amount,
        canonical_unit=canonical,
        unit_price_paise=unit_price,
        line_total_paise=one_pack_price_paise * packs,
        overbuy_qty=overbuy,
        overbuy_unit=overbuy_unit,
    )


def best_offer(item: ParsedItem, platform: str, candidates: tuple[Candidate, ...]) -> NormalizedOffer | None:
    """Pick the cheapest satisfying offer; tie-break by score, then SKU."""
    offers = [
        normalized
        for candidate in candidates
        if (normalized := normalize_candidate(platform, item, candidate)) is not None
    ]
    if not offers:
        return None
    return min(offers, key=lambda offer: (
        offer.line_total_paise,
        -offer.candidate.match_score,
        offer.candidate.sku_id,
    ))

