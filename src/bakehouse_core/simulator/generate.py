"""Build one virtual day of raw data (spec section 3.2). Pure Python, no Spark.

Rows are plain dicts with the same columns as `samples.bakehouse`. Everything is derived from
`(seed, virtual_date)` alone, so a day can be regenerated, and one run of N days gives the
same data as N runs of one day.

Two deliberate differences from the sample:
- `cardNumber` is dropped from transactions, because nothing downstream needs it.
- Generated customers use made-up names and `example.com` addresses.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from bakehouse_core.simulator.clock import START_DATE, DayPlan

Row = dict[str, Any]

ENTITIES = ("transactions", "customers", "franchises", "suppliers", "reviews")

VOLUME_JITTER = 0.2  # synthetic days have the template's volume +/- 20%
PRICE_DRIFT_PER_DAY = 0.002  # unit prices rise 0.2% a day after the real data ends
REVIEWS_PER_DAY = (3, 8)
NEW_CUSTOMER_CHANCE = 0.3
CUSTOMER_CHANGE_CHANCE = 0.3
FRANCHISE_CHANGE_CHANCE = 0.05
SUPPLIER_CHANGE_CHANCE = 0.05

# Generated ids start well above the sample's so they never collide with real ones.
_ID_BASE = 1_000_000_000
_IDS_PER_DAY = 1_000_000

_FIRST_NAMES = ("Alex", "Sam", "Jo", "Robin", "Kit", "Morgan", "Casey", "Drew")
_LAST_NAMES = ("Baker", "Miller", "Cook", "Fisher", "Thatcher", "Mason", "Cooper", "Wheeler")


@dataclass(frozen=True)
class SourceData:
    """The real sample data the simulator draws from."""

    transactions: Mapping[date, Sequence[Row]]  # real days only
    customers: Sequence[Row]
    franchises: Sequence[Row]
    suppliers: Sequence[Row]
    reviews: Sequence[Row]  # the pool of real review text


def generate_day(plan: DayPlan, seed: int, source: SourceData) -> dict[str, list[Row]]:
    """All rows for one day, keyed by entity (every entity is present, possibly empty)."""
    rng = random.Random(f"{seed}:{plan.virtual_date.isoformat()}")
    day_index = (plan.virtual_date - START_DATE).days

    if plan.source_date is not None:
        transactions = _replay_transactions(plan, source)
    else:
        transactions = _synthetic_transactions(plan, rng, source, day_index)

    first_day = plan.virtual_date == START_DATE
    return {
        "transactions": transactions,
        "customers": _customer_rows(plan, rng, source, day_index, first_day),
        "franchises": _snapshot_or_changes(
            source.franchises, first_day, rng, FRANCHISE_CHANGE_CHANCE, "size"
        ),
        "suppliers": _snapshot_or_changes(
            source.suppliers, first_day, rng, SUPPLIER_CHANGE_CHANCE, "approved"
        ),
        "reviews": _reviews(plan, rng, source, day_index),
    }


def _without_card(row: Row) -> Row:
    return {k: v for k, v in row.items() if k != "cardNumber"}


def _replay_transactions(plan: DayPlan, source: SourceData) -> list[Row]:
    assert plan.source_date is not None
    rows = source.transactions.get(plan.source_date)
    if rows is None:
        raise ValueError(f"no sample transactions for {plan.source_date}")
    shift = plan.virtual_date - plan.source_date
    return [{**_without_card(r), "dateTime": r["dateTime"] + shift} for r in rows]


def _synthetic_transactions(
    plan: DayPlan, rng: random.Random, source: SourceData, day_index: int
) -> list[Row]:
    # Use a real day of the same kind (weekday or weekend) as the template, so the shape
    # of the day matches.
    is_weekend = plan.virtual_date.weekday() >= 5
    days = sorted(source.transactions)
    same_kind = [d for d in days if (d.weekday() >= 5) == is_weekend]
    template = source.transactions[rng.choice(same_kind or days)]

    count = round(len(template) * rng.uniform(1 - VOLUME_JITTER, 1 + VOLUME_JITTER))
    days_synthetic = max(day_index - len(days), 0) + 1
    drift = 1 + PRICE_DRIFT_PER_DAY * days_synthetic

    rows = []
    for n, row in enumerate(rng.choices(template, k=count)):
        unit_price = max(1, round(row["unitPrice"] * drift))
        rows.append(
            {
                **_without_card(row),
                "transactionID": _ID_BASE + day_index * _IDS_PER_DAY + n,
                "dateTime": datetime.combine(plan.virtual_date, row["dateTime"].time()),
                "unitPrice": unit_price,
                "totalPrice": unit_price * row["quantity"],
            }
        )
    return rows


def _customer_rows(
    plan: DayPlan, rng: random.Random, source: SourceData, day_index: int, first_day: bool
) -> list[Row]:
    rows = [dict(r) for r in source.customers] if first_day else []
    if not source.customers:
        return rows

    if rng.random() < CUSTOMER_CHANGE_CHANCE:
        # Same customer, new address: this is what slowly changing dimension logic must keep.
        base = dict(rng.choice(list(source.customers)))
        donor = rng.choice(list(source.customers))
        base.update(city=donor["city"], state=donor["state"], country=donor["country"])
        base["address"] = f"{rng.randint(1, 999)} Changed Street"
        rows.append(base)

    if rng.random() < NEW_CUSTOMER_CHANCE:
        template = dict(rng.choice(list(source.customers)))
        customer_id = _ID_BASE + day_index * _IDS_PER_DAY
        first, last = rng.choice(_FIRST_NAMES), rng.choice(_LAST_NAMES)
        template.update(
            customerID=customer_id,
            first_name=first,
            last_name=last,
            email_address=f"{first}.{last}.{customer_id}@example.com".lower(),
            phone_number=f"+00-555-{rng.randint(0, 9999):04d}",
        )
        rows.append(template)
    return rows


def _snapshot_or_changes(
    rows: Sequence[Row], first_day: bool, rng: random.Random, chance: float, column: str
) -> list[Row]:
    """The full table on the first day, then an occasional change to one categorical column."""
    out = [dict(r) for r in rows] if first_day else []
    if rows and rng.random() < chance:
        changed = dict(rng.choice(list(rows)))
        options = sorted({r[column] for r in rows if r[column] != changed[column]})
        if options:
            changed[column] = rng.choice(options)
            out.append(changed)
    return out


def _reviews(plan: DayPlan, rng: random.Random, source: SourceData, day_index: int) -> list[Row]:
    if not source.reviews:
        return []
    count = rng.randint(*REVIEWS_PER_DAY)
    rows = []
    for n, pooled in enumerate(rng.choices(list(source.reviews), k=count)):
        when = datetime.combine(plan.virtual_date, datetime.min.time()) + timedelta(
            seconds=rng.randint(0, 86399)
        )
        rows.append(
            {
                "review": pooled["review"],
                "franchiseID": pooled["franchiseID"],
                "review_date": when,
                "new_id": day_index * 1000 + n,
            }
        )
    return rows
