from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from bakehouse_core.params import model
from bakehouse_core.params.repository import ParamValidationError, SqlRepository
from bakehouse_core.params.schema import init_schema, param_definitions, param_versions


@pytest.fixture
def engine():
    engine = sa.create_engine("sqlite://")
    init_schema(engine)
    return engine


@pytest.fixture
def repo(engine):
    return SqlRepository(engine)


def test_seed_loads_defaults(repo):
    assert repo.load_current() == model.defaults()


def test_init_schema_is_idempotent(engine, repo):
    init_schema(engine)
    with engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(param_definitions)) == 10
        assert conn.scalar(sa.select(sa.func.count()).select_from(param_versions)) == 10


def test_init_schema_does_not_overwrite_edits(engine, repo):
    repo.save({"min_txn_for_ranking": 75}, user="tim")
    init_schema(engine)
    assert repo.load_current()["min_txn_for_ranking"] == 75


def test_save_adds_a_version_and_keeps_history(repo):
    versions = repo.save({"min_txn_for_ranking": "75"}, user="tim", note="busier shops")
    assert versions == {"min_txn_for_ranking": 2}
    assert repo.load_current()["min_txn_for_ranking"] == 75

    history = repo.history("min_txn_for_ranking")
    assert [h.version for h in history] == [1, 2]
    assert [h.value for h in history] == [50, 75]
    assert history[1].updated_by == "tim"
    assert history[1].change_note == "busier shops"


def test_decimal_values_round_trip_exactly(repo):
    repo.save({"underperform_pct": "0.25"}, user="tim")
    assert repo.load_current()["underperform_pct"] == Decimal("0.25")


def test_as_of_picks_the_version_in_force(repo):
    repo.save({"min_txn_for_ranking": 60}, user="tim", effective_from=date(2024, 5, 5))
    repo.save({"min_txn_for_ranking": 70}, user="tim", effective_from=date(2024, 5, 10))

    assert repo.load_as_of(date(2024, 5, 1))["min_txn_for_ranking"] == 50
    assert repo.load_as_of(date(2024, 5, 5))["min_txn_for_ranking"] == 60
    assert repo.load_as_of(date(2024, 5, 9))["min_txn_for_ranking"] == 60
    assert repo.load_as_of(date(2024, 5, 10))["min_txn_for_ranking"] == 70
    assert repo.load_current()["min_txn_for_ranking"] == 70


def test_invalid_change_saves_nothing(repo):
    with pytest.raises(ParamValidationError) as exc:
        repo.save({"min_txn_for_ranking": 75, "loyalty_lookback_days": 3}, user="tim")
    assert "loyalty_lookback_days" in exc.value.errors
    assert repo.load_current() == model.defaults()
    assert len(repo.history("min_txn_for_ranking")) == 1


def test_cross_field_rule_is_checked_against_stored_values(repo):
    with pytest.raises(ParamValidationError):
        repo.save({"loyalty_silver_min_spend": 300}, user="tim")


def test_empty_save_does_nothing(repo):
    assert repo.save({}, user="tim") == {}


def test_save_takes_the_next_free_version_per_key(engine, repo):
    # Another writer has already added version 2 of one key.
    with engine.begin() as conn:
        conn.execute(
            param_versions.insert().values(
                param_key="review_alert_min_count",
                version=2,
                value_text="4",
                effective_from=date(2024, 5, 1),
                updated_by="other",
                updated_at=sa.func.now(),
                change_note="",
            )
        )
    # Each key's version is worked out per key inside the save transaction.
    versions = repo.save({"min_txn_for_ranking": 75, "review_alert_min_count": 5}, user="tim")
    assert versions == {"min_txn_for_ranking": 2, "review_alert_min_count": 3}


def test_ensure_schema_creates_and_seeds_a_fresh_database():
    fresh = SqlRepository(sa.create_engine("sqlite://"))
    fresh.ensure_schema()
    assert fresh.load_current() == model.defaults()
    fresh.ensure_schema()  # safe to repeat
    assert len(fresh.history("underperform_pct")) == 1
