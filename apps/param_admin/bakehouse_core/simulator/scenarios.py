"""Injectable scenarios (spec section 3.2).

Each scenario takes a generated day and returns a changed copy that exercises a data quality
rule or one of the 10 parameters. They are switched on by name for a run (the `scenarios` job
parameter), never at random, so a demo is predictable. Like the generator, each one depends
only on `(seed, virtual_date)`.

- `late_files`: 15% of transactions get an event time 1-3 days before the file date.
  Exercises the late-file report.
- `duplicates`: 5% of transactions appear twice. Exercises deduplication.
- `null_spike`: `paymentMethod` is null on 10% of transactions. Exercises `dq_max_null_pct`.
- `underperforming_franchise`: one franchise loses half its transactions, every day.
  Exercises `underperform_pct`.
- `negative_review_burst`: 20 extra negative reviews for one franchise.
  Exercises `review_alert_min_count`.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable
from datetime import datetime, timedelta

from bakehouse_core.simulator.clock import START_DATE, DayPlan
from bakehouse_core.simulator.generate import Row, SourceData

SCENARIOS = (
    "late_files",
    "underperforming_franchise",
    "null_spike",
    "duplicates",
    "negative_review_burst",
)

LATE_FRACTION = 0.15
LATE_MAX_DAYS = 3
DUPLICATE_FRACTION = 0.05
NULL_FRACTION = 0.10
UNDERPERFORM_DROP = 0.5
BURST_REVIEWS = 20

NEGATIVE_REVIEWS = (
    "Terrible service and the pastries were stale.",
    "Cold coffee, rude staff, never again.",
    "Waited 30 minutes and the order was wrong.",
    "Overpriced and disappointing.",
    "The bread was burnt and nobody cared.",
)


def parse_scenarios(text: str) -> list[str]:
    """Turn the job parameter, e.g. 'duplicates, null_spike', into a validated list.

    Names may be separated by commas, semicolons or spaces. The CLI's `--params` uses commas
    to separate parameters, so `scenarios=duplicates;null_spike` is the form to use there.
    """
    names = [n for n in re.split(r"[,;\s]+", text) if n]
    unknown = sorted(set(names) - set(SCENARIOS))
    if unknown:
        raise ValueError(
            f"unknown scenario(s) {', '.join(unknown)}; choose from {', '.join(SCENARIOS)}"
        )
    return names


def target_franchise(seed: int, source: SourceData, salt: str) -> int:
    """A franchise chosen from `seed` alone, so it is the same on every day."""
    ids = sorted(f["franchiseID"] for f in source.franchises)
    return random.Random(f"{seed}:{salt}").choice(ids)


def apply_scenarios(
    day: dict[str, list[Row]],
    plan: DayPlan,
    seed: int,
    source: SourceData,
    names: Iterable[str],
) -> dict[str, list[Row]]:
    """Apply the named scenarios in a fixed order, whatever order they are given in."""
    wanted = set(parse_scenarios(",".join(names)))
    out = {entity: [dict(r) for r in rows] for entity, rows in day.items()}
    for name in SCENARIOS:
        if name in wanted:
            rng = random.Random(f"{seed}:{plan.virtual_date.isoformat()}:{name}")
            _SCENARIOS[name](out, plan, seed, source, rng)
    return out


def _late_files(day, plan, seed, source, rng):
    for row in day["transactions"]:
        if rng.random() < LATE_FRACTION:
            row["dateTime"] -= timedelta(days=rng.randint(1, LATE_MAX_DAYS))


def _underperforming_franchise(day, plan, seed, source, rng):
    franchise = target_franchise(seed, source, "underperformer")
    day["transactions"] = [
        r
        for r in day["transactions"]
        if r["franchiseID"] != franchise or rng.random() >= UNDERPERFORM_DROP
    ]


def _null_spike(day, plan, seed, source, rng):
    for row in day["transactions"]:
        if rng.random() < NULL_FRACTION:
            row["paymentMethod"] = None


def _duplicates(day, plan, seed, source, rng):
    day["transactions"] += [
        dict(r) for r in day["transactions"] if rng.random() < DUPLICATE_FRACTION
    ]


def _negative_review_burst(day, plan, seed, source, rng):
    franchise = target_franchise(seed, source, "review_burst")
    day_index = (plan.virtual_date - START_DATE).days
    midnight = datetime.combine(plan.virtual_date, datetime.min.time())
    for n in range(BURST_REVIEWS):
        day["reviews"].append(
            {
                "review": rng.choice(NEGATIVE_REVIEWS),
                "franchiseID": franchise,
                "review_date": midnight + timedelta(seconds=rng.randint(0, 86399)),
                # Regular reviews use n < 8, so these ids cannot collide with them.
                "new_id": day_index * 1000 + 500 + n,
            }
        )


_SCENARIOS = {
    "late_files": _late_files,
    "underperforming_franchise": _underperforming_franchise,
    "null_spike": _null_spike,
    "duplicates": _duplicates,
    "negative_review_burst": _negative_review_burst,
}
