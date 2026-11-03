from datetime import date, datetime, timedelta

import pytest

from bakehouse_core.simulator import clock, generate
from bakehouse_core.simulator.clock import ClockState, DayPlan


def _transactions(day: date, n: int, price: int = 10) -> list[dict]:
    return [
        {
            "transactionID": day.day * 1000 + i,
            "customerID": 1 + i % 3,
            "franchiseID": 100 + i % 2,
            "dateTime": datetime.combine(day, datetime.min.time()) + timedelta(hours=8 + i % 10),
            "product": "Golden Gate Ginger",
            "quantity": 2,
            "unitPrice": price,
            "totalPrice": 2 * price,
            "paymentMethod": "visa",
            "cardNumber": 4000000000000000 + i,
        }
        for i in range(n)
    ]


@pytest.fixture
def source():
    # 1-17 May 2024, 100 rows on weekdays and 200 on weekends, so the template kind is visible.
    days = {
        date(2024, 5, d): _transactions(
            date(2024, 5, d), 200 if date(2024, 5, d).weekday() >= 5 else 100
        )
        for d in range(1, 18)
    }
    customers = [
        {
            "customerID": i,
            "first_name": "A",
            "last_name": "B",
            "email_address": "a@example.com",
            "phone_number": "1",
            "address": "1 Road",
            "city": f"City{i}",
            "state": f"State{i}",
            "country": f"Country{i}",
            "continent": "X",
            "postal_zip_code": 1,
            "gender": "f",
        }
        for i in (1, 2, 3)
    ]
    franchises = [
        {"franchiseID": 100, "name": "F1", "size": "S"},
        {"franchiseID": 101, "name": "F2", "size": "M"},
    ]
    suppliers = [
        {"supplierID": 1, "name": "S1", "approved": "Y"},
        {"supplierID": 2, "name": "S2", "approved": "N"},
    ]
    reviews = [
        {"review": f"text {i}", "franchiseID": 100 + i % 2, "review_date": None} for i in range(5)
    ]
    return generate.SourceData(days, customers, franchises, suppliers, reviews)


def _plan(day: date, mode: str = "auto") -> DayPlan:
    state = ClockState(virtual_date=day - timedelta(days=1))
    return clock.plan_days(state, 1, mode)[0]


def test_every_entity_is_present(source):
    day = generate.generate_day(_plan(date(2024, 5, 3)), 42, source)
    assert tuple(day) == generate.ENTITIES


def test_replay_returns_the_real_rows_without_card_numbers(source):
    day = generate.generate_day(_plan(date(2024, 5, 2)), 42, source)
    rows = day["transactions"]
    assert len(rows) == 100
    assert all("cardNumber" not in r for r in rows)
    assert [r["transactionID"] for r in rows] == [
        r["transactionID"] for r in source.transactions[date(2024, 5, 2)]
    ]


def test_replay_shifts_rows_onto_the_virtual_date(source):
    plan = DayPlan(date(2024, 6, 1), "replay", date(2024, 5, 2))
    rows = generate.generate_day(plan, 42, source)["transactions"]
    assert {r["dateTime"].date() for r in rows} == {date(2024, 6, 1)}


def test_replay_of_a_missing_sample_day_fails_clearly(source):
    plan = DayPlan(date(2024, 5, 20), "replay", date(2024, 5, 20))
    with pytest.raises(ValueError, match="no sample transactions"):
        generate.generate_day(plan, 42, source)


def test_same_seed_and_date_give_identical_data(source):
    plan = _plan(date(2024, 5, 25))
    assert generate.generate_day(plan, 42, source) == generate.generate_day(plan, 42, source)


def test_different_seed_or_date_gives_different_data(source):
    a = generate.generate_day(_plan(date(2024, 5, 25)), 42, source)
    b = generate.generate_day(_plan(date(2024, 5, 25)), 43, source)
    c = generate.generate_day(_plan(date(2024, 5, 26)), 42, source)
    assert a["transactions"] != b["transactions"]
    assert a["transactions"] != c["transactions"]


def test_synthetic_rows_sit_on_the_virtual_date_with_new_unique_ids(source):
    plan = _plan(date(2024, 6, 3))
    rows = generate.generate_day(plan, 42, source)["transactions"]
    assert {r["dateTime"].date() for r in rows} == {date(2024, 6, 3)}
    ids = [r["transactionID"] for r in rows]
    assert len(set(ids)) == len(ids)
    real_ids = {t["transactionID"] for ts in source.transactions.values() for t in ts}
    assert not set(ids) & real_ids
    assert all("cardNumber" not in r for r in rows)


def test_ids_do_not_repeat_across_days(source):
    a = generate.generate_day(_plan(date(2024, 6, 3)), 42, source)["transactions"]
    b = generate.generate_day(_plan(date(2024, 6, 4)), 42, source)["transactions"]
    assert not {r["transactionID"] for r in a} & {r["transactionID"] for r in b}


def test_synthetic_volume_stays_within_jitter_of_the_template_kind(source):
    for day in (date(2024, 6, 3), date(2024, 6, 4), date(2024, 6, 8), date(2024, 6, 9)):
        weekend = day.weekday() >= 5
        base = 200 if weekend else 100
        n = len(generate.generate_day(_plan(day), 42, source)["transactions"])
        assert base * 0.8 - 1 <= n <= base * 1.2 + 1


def test_prices_drift_up_and_totals_stay_consistent(source):
    early = generate.generate_day(_plan(date(2024, 5, 18)), 42, source)["transactions"]
    late = generate.generate_day(_plan(date(2024, 8, 18)), 42, source)["transactions"]
    assert max(r["unitPrice"] for r in late) > max(r["unitPrice"] for r in early)
    for r in early + late:
        assert r["unitPrice"] >= 1
        assert r["totalPrice"] == r["unitPrice"] * r["quantity"]


def test_synthetic_mode_inside_the_replay_window(source):
    rows = generate.generate_day(_plan(date(2024, 5, 5), "synthetic"), 42, source)["transactions"]
    assert {r["dateTime"].date() for r in rows} == {date(2024, 5, 5)}
    assert min(r["transactionID"] for r in rows) >= 1_000_000_000


def test_reference_tables_are_a_snapshot_on_the_first_day_only(source):
    first = generate.generate_day(_plan(date(2024, 5, 1)), 42, source)
    assert len(first["customers"]) >= len(source.customers)
    assert len(first["franchises"]) >= len(source.franchises)
    assert len(first["suppliers"]) >= len(source.suppliers)
    for d in range(2, 30):
        later = generate.generate_day(_plan(date(2024, 5, d)), 42, source)
        assert len(later["customers"]) <= 2
        assert len(later["franchises"]) <= 1
        assert len(later["suppliers"]) <= 1


def test_customer_changes_and_new_customers_show_up_over_time(source):
    known = {c["customerID"]: c for c in source.customers}
    changed = new = 0
    for d in range(2, 60):
        for row in generate.generate_day(_plan(date(2024, 5, 1) + timedelta(days=d)), 42, source)[
            "customers"
        ]:
            if row["customerID"] in known:
                changed += 1
                assert row["city"] != known[row["customerID"]]["city"] or row["address"] != "1 Road"
            else:
                new += 1
                assert row["email_address"].endswith("@example.com")
    assert changed > 0
    assert new > 0


def test_new_customer_ids_are_unique(source):
    ids = []
    for d in range(2, 80):
        rows = generate.generate_day(_plan(date(2024, 5, 1) + timedelta(days=d)), 42, source)
        ids += [r["customerID"] for r in rows["customers"] if r["customerID"] > 1000]
    assert len(ids) == len(set(ids))


def test_dimension_changes_pick_a_different_value(source):
    seen = 0
    for d in range(2, 400):
        rows = generate.generate_day(_plan(date(2024, 5, 1) + timedelta(days=d)), 7, source)
        for f in rows["franchises"]:
            original = next(x for x in source.franchises if x["franchiseID"] == f["franchiseID"])
            assert f["size"] != original["size"]
            seen += 1
    assert seen > 0


def test_reviews_are_dated_on_the_virtual_date_with_unique_ids(source):
    ids = []
    for d in range(0, 20):
        day = date(2024, 5, 1) + timedelta(days=d)
        rows = generate.generate_day(_plan(day), 42, source)["reviews"]
        assert generate.REVIEWS_PER_DAY[0] <= len(rows) <= generate.REVIEWS_PER_DAY[1]
        assert {r["review_date"].date() for r in rows} == {day}
        ids += [r["new_id"] for r in rows]
    assert len(ids) == len(set(ids))


def test_a_backfill_equals_day_by_day(source):
    state = ClockState.initial()
    plans = clock.plan_days(state, 25)
    together = [generate.generate_day(p, state.seed, source) for p in plans]
    one_by_one = []
    for _ in range(25):
        [p] = clock.plan_days(state)
        one_by_one.append(generate.generate_day(p, state.seed, source))
        state = clock.advance(state, [p])
    assert together == one_by_one
