"""Deterministic list parser used when ASI-1 is not configured or fails."""

from __future__ import annotations

import re
import unicodedata

from basketpilot.models import ParsedItem, ParsedList

_ITEM_RE = re.compile(
    r"^\s*(?:(?P<count>\d+(?:\.\d+)?)\s*(?:x|×)\s*)?"
    r"(?:(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>kg|kgs?|g|gm|gms|l|lt|lts|ml|dozen|dz|pcs?|pieces?)\b\s*)?"
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
    chunks = [part.strip(" \t-*•") for part in re.split(r"[,\n;]+", text) if part.strip(" \t-*•")]

    for index, chunk in enumerate(chunks, start=1):
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
        count_text = match.group("count") or match.group("suffix_count")
        if qty_text is not None:
            qty = float(qty_text)
            unit = _UNIT_MAP[unit_text.lower()]
        elif count_text is not None:
            qty = float(count_text)
            unit = "piece"
        else:
            qty = 1.0
            unit = None

        if qty <= 0:
            ambiguities.append(chunk)
            continue
        items.append(ParsedItem(id=f"item-{len(items) + 1}", name=name, qty=qty, unit=unit))

    return ParsedList(items=tuple(items), ambiguities=tuple(ambiguities))

