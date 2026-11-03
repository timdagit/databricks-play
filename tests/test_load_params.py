from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from bakehouse_core.jobs.load_params import NoVirtualDateError, run_load_params
from bakehouse_core.jobs.store import InMemoryOpsStore
from bakehouse_core.params import model, snapshot
from bakehouse_core.params.repository import SqlRepository
from bakehouse_core.params.schema import init_schema
from bakehouse_core.simulator.clock import ClockState


@pytest.fixture
def repo():
    engine = sa.create_engine("sqlite://")
    init_schema(engine)
    return SqlRepository(engine)


def test_versions_as_of_follow_the_effective_date(repo):
    repo.save({"underperform_pct": "0.30"}, user="t", effective_from=date(2024, 5, 10))
    assert repo.versions_as_of(date(2024, 5, 9))["underperform_pct"] == 1
    assert repo.versions_as_of(date(2024, 5, 10))["underperform_pct"] == 2
    assert repo.versions_as_of(date(2024, 5, 10))["review_alert_min_count"] == 1


def test_snapshot_round_trips_typed_values():
    values = model.defaults()
    versions = {k: 1 for k in values}
    text = snapshot.to_json(date(2024, 5, 3), values, versions)
    assert snapshot.values_from_details(text) == values


def test_run_load_params_logs_values_and_versions(repo):
    repo.save({"underperform_pct": "0.30"}, user="t", effective_from=date(2024, 5, 2))
    store = InMemoryOpsStore()
    store.clock = ClockState(virtual_date=date(2024, 5, 3), seed=42)
    entry = run_load_params(store, repo, run_id="r1", triggered_by="tim")
    assert store.find_run("r1", "load_params") == entry
    assert entry.virtual_date == date(2024, 5, 3)
    assert entry.triggered_by == "tim"
    values = snapshot.values_from_details(entry.details)
    assert values["underperform_pct"] == Decimal("0.30")


def test_each_virtual_date_gets_its_own_parameters(repo):
    repo.save({"underperform_pct": "0.30"}, user="t", effective_from=date(2024, 5, 10))
    store = InMemoryOpsStore()
    store.clock = ClockState(virtual_date=date(2024, 5, 5), seed=42)
    early = run_load_params(store, repo, run_id="a")
    store.clock = ClockState(virtual_date=date(2024, 5, 12), seed=42)
    late = run_load_params(store, repo, run_id="b")
    assert snapshot.values_from_details(early.details)["underperform_pct"] == Decimal("0.20")
    assert snapshot.values_from_details(late.details)["underperform_pct"] == Decimal("0.30")


def test_requires_a_released_day(repo):
    store = InMemoryOpsStore()
    with pytest.raises(NoVirtualDateError):
        run_load_params(store, repo)
    store.clock = ClockState.initial()
    with pytest.raises(NoVirtualDateError):
        run_load_params(store, repo)
