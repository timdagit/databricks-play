from collections import Counter
from datetime import date, datetime, timedelta

import pytest

from bakehouse_core.simulator import clock, generate, scenarios
from bakehouse_core.simulator.clock import ClockState, DayPlan

SEED = 42


def _transactions(day: date, n: int) -> list[dict]:
    return [
        {
            "transactionID": day.day * 10_000 + i,
            "customerID": 1 + i % 3,
            "franchiseID": 100 + i % 4,
            "dateTime": datetime.combine(day, datetime.min.time()) + timedelta(hours=8 + i % 10),
            "product": "Golden Gate Ginger",
            "quantity": 2,
            "unitPrice": 10,
            "totalPrice": 20,
            "paymentMethod": "visa",
        }
        for i in range(n)
    ]


@pytest.fixture
def source():
    days = {date(2024, 5, d): _transactions(date(2024, 5, d), 400) for d in range(1, 18)}
    return generate.SourceData(
        transactions=days,
        customers=[
            {"customerID": i, "city": "c", "state": "s", "country": "x", "address": "a"}
            for i in (1, 2, 3)
        ],
        franchises=[{"franchiseID": 100 + i, "name": f"F{i}", "size": "S"} for i in range(4)],
        suppliers=[{"supplierID": 1, "approved": "Y"}],
        reviews=[{"review": "fine", "franchiseID": 100, "review_date": None}],
    )


def _day(source, when=date(2024, 5, 5)):
    plan = clock.plan_days(ClockState(virtual_date=when - timedelta(days=1)))[0]
    return plan, generate.generate_day(plan, SEED, source)


def _apply(source, *names, when=date(2024, 5, 5)):
    plan, day = _day(source, when)
    return day, scenarios.apply_scenarios(day, plan, SEED, source, names)


def test_no_scenarios_changes_nothing(source):
    day, out = _apply(source)
    assert out == day


def test_input_is_not_mutated(source):
    plan, day = _day(source)
    before = {e: [dict(r) for r in rows] for e, rows in day.items()}
    scenarios.apply_scenarios(day, plan, SEED, source, scenarios.SCENARIOS)
    assert day == before


def test_late_files_move_event_times_before_the_file_date(source):
    day, out = _apply(source, "late_files")
    partition = date(2024, 5, 5)
    late = [r for r in out["transactions"] if r["dateTime"].date() < partition]
    assert 0.08 * 400 < len(late) < 0.22 * 400
    assert all(partition - r["dateTime"].date() <= timedelta(days=3) for r in late)
    assert len(out["transactions"]) == len(day["transactions"])


def test_duplicates_repeat_whole_rows(source):
    day, out = _apply(source, "duplicates")
    counts = Counter(r["transactionID"] for r in out["transactions"])
    dupes = [i for i, c in counts.items() if c > 1]
    assert 0.02 * 400 < len(dupes) < 0.09 * 400
    assert max(counts.values()) == 2
    assert len(out["transactions"]) == len(day["transactions"]) + len(dupes)


def test_null_spike_exceeds_the_default_dq_threshold(source):
    from bakehouse_core.params import model

    _, out = _apply(source, "null_spike")
    rows = out["transactions"]
    share = sum(r["paymentMethod"] is None for r in rows) / len(rows)
    assert share > float(model.BY_KEY["dq_max_null_pct"].default)


def test_underperformer_loses_about_half_and_others_are_untouched(source):
    day, out = _apply(source, "underperforming_franchise")
    victim = scenarios.target_franchise(SEED, source, "underperformer")
    before = Counter(r["franchiseID"] for r in day["transactions"])
    after = Counter(r["franchiseID"] for r in out["transactions"])
    assert 0.35 < after[victim] / before[victim] < 0.65
    for franchise in before:
        if franchise != victim:
            assert after[franchise] == before[franchise]


def test_underperformer_is_the_same_franchise_every_day(source):
    victims = set()
    for d in range(1, 10):
        day, out = _apply(source, "underperforming_franchise", when=date(2024, 5, d))
        before = Counter(r["franchiseID"] for r in day["transactions"])
        after = Counter(r["franchiseID"] for r in out["transactions"])
        victims |= {f for f in before if after[f] < before[f]}
    assert victims == {scenarios.target_franchise(SEED, source, "underperformer")}


def test_review_burst_adds_negative_reviews_for_one_franchise(source):
    day, out = _apply(source, "negative_review_burst")
    extra = out["reviews"][len(day["reviews"]) :]
    assert len(extra) == scenarios.BURST_REVIEWS
    assert {r["franchiseID"] for r in extra} == {
        scenarios.target_franchise(SEED, source, "review_burst")
    }
    assert {r["review"] for r in extra} <= set(scenarios.NEGATIVE_REVIEWS)
    ids = [r["new_id"] for r in out["reviews"]]
    assert len(ids) == len(set(ids))
    assert {r["review_date"].date() for r in extra} == {date(2024, 5, 5)}


def test_burst_is_large_enough_to_trip_the_default_alert(source):
    from bakehouse_core.params import model

    assert scenarios.BURST_REVIEWS > model.BY_KEY["review_alert_min_count"].default


def test_result_does_not_depend_on_the_order_names_are_given(source):
    _, a = _apply(source, "duplicates", "null_spike", "late_files")
    _, b = _apply(source, "late_files", "null_spike", "duplicates")
    assert a == b


def test_scenarios_are_reproducible(source):
    _, a = _apply(source, *scenarios.SCENARIOS)
    _, b = _apply(source, *scenarios.SCENARIOS)
    assert a == b


def test_all_scenarios_work_on_a_synthetic_day(source):
    plan = DayPlan(date(2024, 6, 1), "synthetic", None)
    day = generate.generate_day(plan, SEED, source)
    out = scenarios.apply_scenarios(day, plan, SEED, source, scenarios.SCENARIOS)
    assert len(out["reviews"]) > len(day["reviews"])


def test_parse_scenarios():
    assert scenarios.parse_scenarios("") == []
    assert scenarios.parse_scenarios(" duplicates , null_spike ") == ["duplicates", "null_spike"]
    assert scenarios.parse_scenarios("duplicates;null_spike") == ["duplicates", "null_spike"]
    assert scenarios.parse_scenarios("duplicates null_spike") == ["duplicates", "null_spike"]
    with pytest.raises(ValueError, match="nope"):
        scenarios.parse_scenarios("duplicates,nope")


def test_unknown_name_is_rejected_by_apply(source):
    plan, day = _day(source)
    with pytest.raises(ValueError, match="nope"):
        scenarios.apply_scenarios(day, plan, SEED, source, ["nope"])
