"""The 10 business parameters, their limits, and validation (spec section 4.1).

Values are stored as text (`value_text`) so every backend holds them the same way.
`Decimal` is used for decimal parameters to avoid float rounding in stored values.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

DataType = Literal["int", "decimal"]
Value = int | Decimal


@dataclass(frozen=True)
class ParamDefinition:
    key: str
    data_type: DataType
    default: Value
    min_value: Value
    max_value: Value
    unit: str
    description: str

    def parse(self, raw: object) -> Value:
        """Convert user input (text or number) to this parameter's type.

        Raises ValueError with a message suitable for showing to the user.
        """
        text = str(raw).strip()
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise ValueError("must be a number") from None
        if not number.is_finite():
            raise ValueError("must be a finite number")
        if self.data_type == "int":
            if number != number.to_integral_value():
                raise ValueError("must be a whole number")
            value: Value = int(number)
        else:
            value = number
        if not self.min_value <= value <= self.max_value:
            raise ValueError(f"must be between {self.min_value} and {self.max_value}")
        return value


def _d(text: str) -> Decimal:
    return Decimal(text)


DEFINITIONS: tuple[ParamDefinition, ...] = (
    ParamDefinition(
        "loyalty_gold_min_spend",
        "decimal",
        _d("250"),
        _d("0"),
        _d("100000"),
        "currency",
        "Minimum spend over the lookback window for the gold loyalty tier.",
    ),
    ParamDefinition(
        "loyalty_silver_min_spend",
        "decimal",
        _d("100"),
        _d("0"),
        _d("100000"),
        "currency",
        "Minimum spend over the lookback window for the silver loyalty tier. "
        "Must be below the gold minimum.",
    ),
    ParamDefinition(
        "loyalty_lookback_days",
        "int",
        90,
        7,
        365,
        "days",
        "Days of history used to work out loyalty tiers.",
    ),
    ParamDefinition(
        "underperform_pct",
        "decimal",
        _d("0.20"),
        _d("0.05"),
        _d("0.50"),
        "fraction",
        "A franchise this far below its peers is flagged as underperforming.",
    ),
    ParamDefinition(
        "min_txn_for_ranking",
        "int",
        50,
        1,
        10000,
        "count",
        "Minimum transactions before a franchise is ranked.",
    ),
    ParamDefinition(
        "promo_discount_rate",
        "decimal",
        _d("0.10"),
        _d("0"),
        _d("0.50"),
        "fraction",
        "Discount applied in the what-if notebook and the gold projection.",
    ),
    ParamDefinition(
        "supplier_concentration_threshold",
        "decimal",
        _d("0.10"),
        _d("0.10"),
        _d("1.00"),
        "fraction",
        "Share of spend with one supplier above which concentration is flagged.",
    ),
    ParamDefinition(
        "review_alert_min_count",
        "int",
        3,
        1,
        50,
        "count",
        "Negative reviews in the 7-day window that raise an alert.",
    ),
    ParamDefinition(
        "dq_max_null_pct",
        "decimal",
        _d("0.02"),
        _d("0"),
        _d("0.50"),
        "fraction",
        "Maximum share of nulls in a silver column before the DQ check fails.",
    ),
    ParamDefinition(
        "sales_target_uplift_pct",
        "decimal",
        _d("0.05"),
        _d("-0.50"),
        _d("1.00"),
        "fraction",
        "Uplift over the previous period used as the sales target.",
    ),
)

BY_KEY: dict[str, ParamDefinition] = {d.key: d for d in DEFINITIONS}


def defaults() -> dict[str, Value]:
    return {d.key: d.default for d in DEFINITIONS}


def to_text(value: Value) -> str:
    """Canonical text form stored in `value_text`."""
    return str(value)


def from_text(key: str, text: str) -> Value:
    """Read a stored value back as its typed form. Does not range-check."""
    definition = BY_KEY[key]
    return int(text) if definition.data_type == "int" else Decimal(text)


def validate_changes(
    current: Mapping[str, Value], changes: Mapping[str, object]
) -> tuple[dict[str, Value], dict[str, str]]:
    """Check proposed changes against the limits and the cross-field rules.

    `current` is the full set of typed values now in force and `changes` holds only the
    keys the user edited, as raw input. Returns `(parsed_changes, errors)`, where `errors`
    maps a parameter key to a message. Nothing should be saved unless `errors` is empty.
    """
    parsed: dict[str, Value] = {}
    errors: dict[str, str] = {}
    for key, raw in changes.items():
        definition = BY_KEY.get(key)
        if definition is None:
            errors[key] = "unknown parameter"
            continue
        try:
            parsed[key] = definition.parse(raw)
        except ValueError as exc:
            errors[key] = str(exc)

    merged = {**current, **parsed}
    gold = merged["loyalty_gold_min_spend"]
    silver = merged["loyalty_silver_min_spend"]
    if (
        "loyalty_silver_min_spend" not in errors
        and "loyalty_gold_min_spend" not in errors
        and silver >= gold
        and ({"loyalty_silver_min_spend", "loyalty_gold_min_spend"} & parsed.keys())
    ):
        errors["loyalty_silver_min_spend"] = "must be below loyalty_gold_min_spend"
    return parsed, errors
