import json
from datetime import date, datetime
from decimal import Decimal

import pytest

from bakehouse_core.io import samples
from bakehouse_core.simulator import writer

DAY = date(2024, 5, 3)


def _day():
    return {
        "transactions": [
            {"transactionID": 1, "dateTime": datetime(2024, 5, 3, 9, 30, 5), "paymentMethod": None},
            {
                "transactionID": 2,
                "dateTime": datetime(2024, 5, 3, 10, 0, 0),
                "paymentMethod": "visa",
            },
        ],
        "customers": [],
        "franchises": [],
        "suppliers": [],
        "reviews": [{"review": "Délicieux ☃", "new_id": 7, "review_date": datetime(2024, 5, 3)}],
    }


def test_relative_path_layout():
    assert (
        writer.relative_path("transactions", DAY)
        == "transactions/date=2024-05-03/transactions.json"
    )


def test_empty_entities_are_skipped():
    assert sorted(writer.day_files(_day(), DAY)) == [
        "reviews/date=2024-05-03/reviews.json",
        "transactions/date=2024-05-03/transactions.json",
    ]


def test_json_lines_round_trip_with_iso_timestamps_and_nulls():
    text = writer.to_json_lines(_day()["transactions"])
    lines = text.splitlines()
    assert text.endswith("\n")
    assert [json.loads(line) for line in lines] == [
        {"transactionID": 1, "dateTime": "2024-05-03T09:30:05", "paymentMethod": None},
        {"transactionID": 2, "dateTime": "2024-05-03T10:00:00", "paymentMethod": "visa"},
    ]


def test_non_ascii_text_is_kept_as_is():
    assert "Délicieux ☃" in writer.to_json_lines(_day()["reviews"])


def test_dates_and_decimals_serialise():
    out = json.loads(writer.to_json_lines([{"d": date(2024, 5, 3), "n": Decimal("0.10")}]))
    assert out == {"d": "2024-05-03", "n": "0.10"}


def test_unserialisable_value_fails_loudly():
    with pytest.raises(TypeError):
        writer.to_json_lines([{"x": object()}])


def test_unknown_entity_is_rejected():
    with pytest.raises(ValueError, match="widgets"):
        writer.day_files({"widgets": [{"a": 1}]}, DAY)


def test_write_day_creates_files_and_is_idempotent(tmp_path):
    first = writer.write_day(_day(), DAY, tmp_path)
    contents = {p: p.read_text() for p in first}
    again = writer.write_day(_day(), DAY, tmp_path)
    assert again == first
    assert {p: p.read_text() for p in again} == contents
    assert (tmp_path / "transactions" / "date=2024-05-03" / "transactions.json").is_file()


def test_delete_day_removes_only_that_day(tmp_path):
    writer.write_day(_day(), DAY, tmp_path)
    writer.write_day(_day(), date(2024, 5, 4), tmp_path)
    writer.delete_day(DAY, tmp_path)
    assert not (tmp_path / "transactions" / "date=2024-05-03").exists()
    assert (tmp_path / "transactions" / "date=2024-05-04" / "transactions.json").is_file()


def test_delete_day_on_an_empty_folder_is_fine(tmp_path):
    writer.delete_day(DAY, tmp_path)


class _Row:
    def __init__(self, **values):
        self._values = values

    def asDict(self):
        return dict(self._values)


class _FakeSpark:
    def __init__(self, tables):
        self._tables = tables
        self.asked = []

    def table(self, name):
        self.asked.append(name)
        rows = self._tables[name.split(".")[-1]]
        return type("Frame", (), {"collect": lambda self: [_Row(**r) for r in rows]})()


def test_load_source_groups_transactions_by_day():
    spark = _FakeSpark(
        {
            "sales_transactions": [
                {"transactionID": 1, "dateTime": datetime(2024, 5, 1, 9)},
                {"transactionID": 2, "dateTime": datetime(2024, 5, 1, 17)},
                {"transactionID": 3, "dateTime": datetime(2024, 5, 2, 9)},
            ],
            "sales_customers": [{"customerID": 1}],
            "sales_franchises": [{"franchiseID": 1}],
            "sales_suppliers": [{"supplierID": 1}],
            "media_customer_reviews": [{"review": "ok", "franchiseID": 1}],
        }
    )
    source = samples.load_source(spark)
    assert sorted(source.transactions) == [date(2024, 5, 1), date(2024, 5, 2)]
    assert len(source.transactions[date(2024, 5, 1)]) == 2
    assert source.customers == [{"customerID": 1}]
    assert all(name.startswith("samples.bakehouse.") for name in spark.asked)
