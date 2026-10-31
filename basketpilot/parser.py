"""Deterministic list parser used when ASI-1 is not configured or fails."""

from __future__ import annotations

import re
import unicodedata

from basketpilot.models import ParsedItem, ParsedList

_ITEM_RE = re.compile(
    r"^\s*(?:(?P<count>\d+(?:\.\d+)?)\s*(?:x|×)\s*)?"
    r"(?:(?P<dozen>(?:a\s+)?dozen)\s+)?"
    r"(?:(?P<qty>\d+(?:/\d+|\.\d+)?)\s*(?P<unit>kg|kgs?|g|gm|gms|l|lt|lts|ml|dozen|dz|pcs?|pieces?)\b\s*)?"
    r"(?:(?P<plain_qty>\d+(?:/\d+|\.\d+)?)\s+)?"
    r"(?P<name>.+?)\s*(?:[x×]\s*(?P<suffix_count>\d+(?:\.\d+)?))?\s*$",
    re.IGNORECASE,
)
_UNIT_MAP = {
    "kgs": "kg", "kg": "kg", "g": "g", "gm": "g", "gms": "g",
    "l": "L", "lt": "L", "lts": "L", "ml": "ml",
    "dz": "dozen", "dozen": "dozen", "pc": "piece", "pcs": "piece",
    "piece": "piece", "pieces": "piece",
}


def parse_list(text: str) -> ParsedList:
    """Parse one item per comma/newline, retaining unclear text as an ambiguity."""
    items: list[ParsedItem] = []
    ambiguities: list[str] = []
    item_positions: dict[tuple[str, str | None, str | None], int] = {}
    observed_units: dict[tuple[str, str | None], set[str | None]] = {}
    chunks = [part.strip(" \t-*•") for part in re.split(r"[,\n;]+", text) if part.strip(" \t-*•")]

    for chunk in chunks:
        match = _ITEM_RE.match(chunk)
        if not match:
            ambiguities.append(chunk)
            continue

        name = unicodedata.normalize("NFKC", match.group("name")).strip(" .")
        if not name:
            ambiguities.append(chunk)
            continue

        unit_text = match.group("unit")
        qty_text = match.group("qty")
        plain_qty_text = match.group("plain_qty")
        count_text = match.group("count") or match.group("suffix_count")
        if match.group("dozen"):
            qty, unit = 12.0, "piece"
        elif qty_text is not None:
            if "/" in qty_text:
                numerator, denominator = qty_text.split("/", 1)
                qty = float(int(numerator) / int(denominator))
            else:
                qty = float(qty_text)
            unit = _UNIT_MAP[unit_text.lower()]
        elif plain_qty_text is not None:
            if "/" in plain_qty_text:
                numerator, denominator = plain_qty_text.split("/", 1)
                qty = float(int(numerator) / int(denominator))
            else:
                qty = float(plain_qty_text)
            unit = None
        elif count_text is not None:
            qty = float(count_text)
            unit = "piece"
        else:
            qty = 1.0
            unit = None

        if qty <= 0:
            ambiguities.append(chunk)
            continue
        normalized_name = _normalize_name(name)
        normalized_brand = None
        unit_key = unit.casefold() if unit is not None else None
        identity = (normalized_name, unit_key, normalized_brand)
        name_brand = (normalized_name, normalized_brand)
        prior_units = observed_units.setdefault(name_brand, set())
        if prior_units and unit_key not in prior_units:
            unit_labels = sorted(value if value is not None else "unspecified" for value in prior_units | {unit_key})
            note = f"{name}: mixed units ({', '.join(unit_labels)}); kept as separate lines."
            if note not in ambiguities:
                ambiguities.append(note)
        prior_units.add(unit_key)

        existing_index = item_positions.get(identity)
        if existing_index is not None:
            existing = items[existing_index]
            items[existing_index] = ParsedItem(
                id=existing.id,
                name=existing.name,
                qty=existing.qty + qty,
                unit=existing.unit,
                brand=existing.brand,
                notes=existing.notes,
            )
        else:
            item = ParsedItem(id=f"item-{len(items) + 1}", name=name, qty=qty, unit=unit)
            item_positions[identity] = len(items)
            items.append(item)

    return ParsedList(items=tuple(items), ambiguities=tuple(ambiguities))


def _normalize_name(name: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", name).casefold().split())
    words = []
    for word in normalized.split():
        if len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        words.append(word)
    return " ".join(words)

