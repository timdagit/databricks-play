"""DeltaRepository against a fake Spark session that runs the SQL on sqlite3.

This checks the statements and the as-of logic. That the same SQL works on Delta is proven on
Databricks.
"""

import re
import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from bakehouse_core.params import model
from bakehouse_core.params.delta import DeltaRepository
from bakehouse_core.params.repository import ParamValidationError


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def collect(self):
        return self._rows


class FakeSpark:
    def __init__(self):
        self._db = sqlite3.connect(":memory:")

    def sql(self, query, args=None):
        # `cat.ops.table` becomes `ops_table`, and `:name` markers are bound by sqlite.
        query = re.sub(r"\b\w+\.ops\.(\w+)", r"ops_\1", query)
        # sqlite would turn a cast to DATE or TIMESTAMP into a number, so keep the text.
        query = re.sub(r"CAST\((:\w+) AS (?:DATE|TIMESTAMP)\)", r"\1", query)
        query = query.replace(" STRING", " TEXT")  # sqlite gives STRING numeric affinity
        cur = self._db.execute(query, args or {})
        return _Result(cur.fetchall())


@pytest.fixture
def repo():
    repo = DeltaRepository(FakeSpark(), "cat")
    repo.ensure_tables()
    return repo


def test_seed_loads_defaults(repo):
    assert repo.load_current() == model.defaults()


def test_ensure_tables_is_idempotent(repo):
    repo.ensure_tables()
    assert len(repo.history("underperform_pct")) == 1
    count = repo._sql("SELECT COUNT(*) FROM cat.ops.param_definitions").collect()[0][0]
    assert count == len(model.DEFINITIONS)


def test_save_adds_versions_and_as_of_picks_by_date(repo):
    versions = repo.save(
        {"underperform_pct": "0.30"}, user="tim", note="stricter", effective_from=date(2024, 5, 10)
    )
    assert versions == {"underperform_pct": 2}
    assert repo.load_current()["underperform_pct"] == Decimal("0.30")
    assert repo.load_as_of(date(2024, 5, 9))["underperform_pct"] == Decimal("0.20")
    assert repo.load_as_of(date(2024, 5, 10))["underperform_pct"] == Decimal("0.30")


def test_save_rejects_invalid_and_saves_nothing(repo):
    with pytest.raises(ParamValidationError):
        repo.save({"underperform_pct": "0.9", "review_alert_min_count": 5}, user="tim")
    assert repo.load_current() == model.defaults()


def test_save_with_nothing_returns_empty(repo):
    assert repo.save({}, user="tim") == {}


def test_history_is_oldest_first(repo):
    repo.save({"review_alert_min_count": 5}, user="a", effective_from=date(2024, 5, 2))
    repo.save({"review_alert_min_count": 7}, user="b", effective_from=date(2024, 5, 3))
    history = repo.history("review_alert_min_count")
    assert [h.version for h in history] == [1, 2, 3]
    assert [h.value for h in history] == [3, 5, 7]
    assert history[1].updated_by == "a"
    assert history[0].effective_from == date(1900, 1, 1)


def test_rejects_unsafe_catalog_name():
    with pytest.raises(ValueError):
        DeltaRepository(FakeSpark(), "x; DROP TABLE y")


def test_versions_as_of_match_the_values_picked(repo):
    repo.save({"underperform_pct": "0.30"}, user="t", effective_from=date(2024, 5, 10))
    assert repo.versions_as_of(date(2024, 5, 9))["underperform_pct"] == 1
    assert repo.versions_as_of(date(2024, 5, 10))["underperform_pct"] == 2


def test_ensure_schema_is_ensure_tables():
    repo = DeltaRepository(FakeSpark(), "cat")
    repo.ensure_schema()
    assert repo.load_current() == model.defaults()
