"""The promo what-if: revenue under a price discount and an assumed volume response.

Plain Python, no Spark. The what-if notebook passes in daily revenue and the two assumptions.
The gold table's `revenue_at_promo` is the special case with no volume change.
"""

from __future__ import annotations

from collections.abc import Sequence

MAX_DISCOUNT = 0.50  # the range of the promo_discount_rate parameter


def _check_discount(discount_rate: float) -> None:
    if not 0 <= discount_rate <= MAX_DISCOUNT:
        raise ValueError(f"discount_rate must be between 0 and {MAX_DISCOUNT}, got {discount_rate}")


def project_revenue(
    revenue: Sequence[float], discount_rate: float, volume_uplift: float = 0.0
) -> list[float]:
    """Revenue if every price had `discount_rate` off and volume changed by `volume_uplift`.

    `volume_uplift` is a fraction: 0.05 means 5% more units sold. Price falls by the discount
    and units rise by the uplift, so each day's revenue is scaled by `(1 - rate) * (1 + uplift)`.
    """
    _check_discount(discount_rate)
    if volume_uplift <= -1:
        raise ValueError(f"volume_uplift must be above -1 (all volume lost), got {volume_uplift}")
    factor = (1 - discount_rate) * (1 + volume_uplift)
    return [r * factor for r in revenue]


def break_even_uplift(discount_rate: float) -> float:
    """The volume uplift at which the discounted revenue equals the original revenue."""
    _check_discount(discount_rate)
    return 1 / (1 - discount_rate) - 1
