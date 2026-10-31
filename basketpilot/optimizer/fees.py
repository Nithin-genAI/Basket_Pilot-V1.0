"""Pure fee calculation in integer paise."""

from __future__ import annotations

from dataclasses import dataclass

from basketpilot.models import FeeSchedule
from basketpilot.optimizer.normalize import rupees_to_paise


@dataclass(frozen=True, slots=True)
class FeeResult:
    lines_paise: dict[str, int]
    feasible: bool
    fees_estimated: bool
    reason: str | None = None

    @property
    def total_paise(self) -> int:
        return sum(self.lines_paise.values())


def compute_fees(subtotal_paise: int, schedule: FeeSchedule | None) -> FeeResult:
    """Return itemized fees and minimum-order feasibility for an item subtotal."""
    if schedule is None:
        return FeeResult(
            lines_paise={name: 0 for name in ("delivery", "handling", "small_cart", "late_night", "other")},
            feasible=True,
            fees_estimated=True,
        )

    exposed_fees = (
        schedule.delivery,
        schedule.handling,
        schedule.small_cart,
        schedule.late_night,
        schedule.other,
    )
    fees_estimated = schedule.fees_estimated or any(value is None for value in exposed_fees)
    delivery = rupees_to_paise(schedule.delivery or 0)
    if schedule.free_delivery_threshold is not None and subtotal_paise >= rupees_to_paise(
        schedule.free_delivery_threshold
    ):
        delivery = 0
    small_cart = 0
    if (
        schedule.small_cart_threshold is not None
        and subtotal_paise < rupees_to_paise(schedule.small_cart_threshold)
    ):
        small_cart = rupees_to_paise(schedule.small_cart or 0)

    min_order_paise = (
        rupees_to_paise(schedule.min_order) if schedule.min_order is not None else None
    )
    feasible = min_order_paise is None or subtotal_paise >= min_order_paise
    reason = None
    if not feasible:
        reason = f"subtotal is below minimum order value Rs {schedule.min_order}"

    return FeeResult(
        lines_paise={
            "delivery": delivery,
            "handling": rupees_to_paise(schedule.handling or 0),
            "small_cart": small_cart,
            "late_night": rupees_to_paise(schedule.late_night or 0),
            "other": rupees_to_paise(schedule.other or 0),
        },
        feasible=feasible,
        fees_estimated=fees_estimated,
        reason=reason,
    )

