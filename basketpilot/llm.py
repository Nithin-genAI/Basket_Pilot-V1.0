"""ASI-1 interface boundary. Step 3B uses deterministic fallbacks only."""

from __future__ import annotations

from basketpilot.models import Candidate, CartPlan, ParsedItem, ParsedList
from basketpilot.parser import parse_list as _parse_list


def parse_list(text: str) -> ParsedList:
    return _parse_list(text)


def rerank(item: ParsedItem, candidates: tuple[Candidate, ...] | list[Candidate]) -> tuple[Candidate, ...]:
    """Stable fallback order: confidence first, then price and SKU."""
    return tuple(sorted(candidates, key=lambda c: (-c.match_score, c.price, c.sku_id)))


def explain_plan(plan: CartPlan) -> str:
    total_rupees = plan.grand_total / 100
    return f"Estimated landed total: Rs {total_rupees:.2f}."
