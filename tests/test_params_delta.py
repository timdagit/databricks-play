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
from bakehouse_core.params.delta import CatalogReadRepository, DeltaRepository
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
        query = re.sub(r"\b\w+\.(?:ops|public|params)\.(\w+)", r"ops_\1", query)
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


def test_catalog_read_repository_reads_what_the_app_saved(repo):
    repo.save({"underperform_pct": "0.30"}, user="t", effective_from=date(2024, 5, 10))
    reader = CatalogReadRepository(repo._spark, "params_cat")
    assert reader.load_as_of(date(2024, 5, 9))["underperform_pct"] == Decimal("0.20")
    assert reader.load_as_of(date(2024, 5, 10))["underperform_pct"] == Decimal("0.30")
    assert reader.versions_as_of(date(2024, 5, 10))["underperform_pct"] == 2


def test_catalog_read_repository_is_read_only_and_creates_nothing():
    spark = FakeSpark()
    reader = CatalogReadRepository(spark, "params_cat")
    reader.ensure_schema()
    reader.ensure_tables()
    assert spark.sql("SELECT name FROM sqlite_master").collect() == []
    with pytest.raises(PermissionError, match="read-only"):
        reader.save({"underperform_pct": "0.30"}, user="t")


class _SparkWithErrorClasses(FakeSpark):
    """Turns sqlite's "no such table" into the error Spark raises for a missing table."""

    def sql(self, query, args=None):
        try:
            return super().sql(query, args)
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                raise RuntimeError("[TABLE_OR_VIEW_NOT_FOUND] The table cannot be found.") from exc
            raise


def test_catalog_read_repository_uses_the_seed_defaults_before_the_app_has_started():
    reader = CatalogReadRepository(_SparkWithErrorClasses(), "params_cat")
    assert reader.load_as_of(date(2024, 5, 1)) == model.defaults()
    assert set(reader.versions_as_of(date(2024, 5, 1)).values()) == {1}
    assert reader.used_defaults


def test_catalog_read_repository_uses_defaults_when_the_tables_are_empty(repo):
    for table in ("param_versions", "param_definitions"):
        repo._sql(f"DELETE FROM cat.ops.{table}")
    reader = CatalogReadRepository(repo._spark, "params_cat")
    assert reader.load_as_of(date(2024, 5, 1)) == model.defaults()
    assert reader.used_defaults


def test_catalog_read_repository_does_not_use_defaults_when_there_is_a_store(repo):
    reader = CatalogReadRepository(repo._spark, "params_cat")
    reader.load_as_of(date(2024, 5, 1))
    assert not reader.used_defaults


def test_the_defaults_are_what_the_store_would_be_seeded_with(repo):
    reader = CatalogReadRepository(_SparkWithErrorClasses(), "params_cat")
    assert reader.versions_as_of(date(2024, 5, 1)) == repo.versions_as_of(date(2024, 5, 1))
    assert reader.load_as_of(date(2024, 5, 1)) == repo.load_as_of(date(2024, 5, 1))


@pytest.mark.parametrize(
    "message",
    [
        "[CATALOG_NOT_FOUND] The catalog params_cat cannot be found.",
        "[SCHEMA_NOT_FOUND] mentions CATALOG_NOT_FOUND too",
        "connection refused",
    ],
)
def test_other_errors_are_not_hidden_by_the_defaults(message):
    class Broken(FakeSpark):
        def sql(self, query, args=None):
            raise RuntimeError(message)

    reader = CatalogReadRepository(Broken(), "params_cat")
    with pytest.raises(RuntimeError):
        reader.load_as_of(date(2024, 5, 1))
    assert not reader.used_defaults
