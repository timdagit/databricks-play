from datetime import date

import pytest

from bakehouse_core.transforms import scd2

pytestmark = pytest.mark.spark

ATTRS = ["city", "email"]


def _current(spark, rows):
    cols = "id long, city string, email string, _row_hash string, _is_current boolean"
    data = [
        (
            i,
            c,
            e,
            spark.createDataFrame([(c, e)], "city string, email string")
            .select(scd2.row_hash(ATTRS))
            .first()[0],
            cur,
        )
        for i, c, e, cur in rows
    ]
    return spark.createDataFrame(data, cols)


def _incoming(spark, rows):
    return spark.createDataFrame(rows, "id long, city string, email string, _virtual_date date")


def _stage(spark, current_rows, incoming_rows):
    staged = scd2.stage_scd2(
        _incoming(spark, incoming_rows), _current(spark, current_rows), "id", ATTRS
    )
    return sorted(
        ((r["id"], r["_merge_key"], r["city"]) for r in staged.collect()),
        key=lambda t: (t[0], t[1] is None),
    )


D = date(2024, 5, 3)


def test_new_key_is_only_inserted(spark):
    assert _stage(spark, [], [(1, "Leeds", "a", D)]) == [(1, None, "Leeds")]


def test_unchanged_key_stages_nothing(spark):
    assert _stage(spark, [(1, "Leeds", "a", True)], [(1, "Leeds", "a", D)]) == []


def test_changed_key_closes_old_and_inserts_new(spark):
    out = _stage(spark, [(1, "Leeds", "a", True)], [(1, "York", "a", D)])
    assert out == [(1, 1, "York"), (1, None, "York")]


def test_only_open_rows_are_compared(spark):
    # The old closed version equals the incoming row, but the open one differs: it is a change.
    current = [(1, "Leeds", "a", False), (1, "York", "a", True)]
    out = _stage(spark, current, [(1, "Leeds", "a", D)])
    assert out == [(1, 1, "Leeds"), (1, None, "Leeds")]


def test_null_attribute_counts_as_a_change(spark):
    out = _stage(spark, [(1, "Leeds", "a", True)], [(1, "Leeds", None, D)])
    assert len(out) == 2


def test_valid_from_comes_from_the_virtual_date(spark):
    staged = scd2.stage_scd2(
        _incoming(spark, [(1, "Leeds", "a", D)]), _current(spark, []), "id", ATTRS
    )
    assert staged.first()["_valid_from"] == D
