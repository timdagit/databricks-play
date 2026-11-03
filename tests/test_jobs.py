import json
from datetime import date, datetime, timedelta

import pytest

from bakehouse_core.io.ops_tables import SparkOpsStore
from bakehouse_core.jobs.reset import run_reset
from bakehouse_core.jobs.simulate import run_simulation
from bakehouse_core.jobs.store import InMemoryOpsStore, RunLogEntry
from bakehouse_core.simulator import generate
from bakehouse_core.simulator.clock import ClockState


def _transactions(day: date, n: int = 50) -> list[dict]:
    return [
        {
            "transactionID": day.day * 10_000 + i,
            "customerID": 1,
            "franchiseID": 100 + i % 2,
            "dateTime": datetime.combine(day, datetime.min.time()) + timedelta(hours=9),
            "product": "p",
            "quantity": 1,
            "unitPrice": 5,
            "totalPrice": 5,
            "paymentMethod": "visa",
        }
        for i in range(n)
    ]


@pytest.fixture
def source():
    return generate.SourceData(
        transactions={date(2024, 5, d): _transactions(date(2024, 5, d)) for d in range(1, 18)},
        customers=[{"customerID": 1, "city": "c", "state": "s", "country": "x", "address": "a"}],
        franchises=[{"franchiseID": 100, "size": "S"}, {"franchiseID": 101, "size": "M"}],
        suppliers=[{"supplierID": 1, "approved": "Y"}],
        reviews=[{"review": "fine", "franchiseID": 100, "review_date": None}],
    )


@pytest.fixture
def store():
    return InMemoryOpsStore(
        tables={"silver": ["customers", "quarantine_sales"], "gold": ["kpis"]},
        ops_tables={"app_metrics"},
    )


# ---- simulator job ----


def test_first_run_releases_day_one_and_advances_the_clock(store, source, tmp_path):
    result = run_simulation(store, source, str(tmp_path), triggered_by="tim")
    assert result.dates == [date(2024, 5, 1)]
    assert store.clock.virtual_date == date(2024, 5, 1)
    assert store.ensured
    assert (tmp_path / "transactions" / "date=2024-05-01" / "transactions.json").is_file()
    [entry] = store.run_log
    assert (entry.job, entry.status, entry.triggered_by) == ("simulator", "succeeded", "tim")
    assert entry.virtual_date == date(2024, 5, 1)
    assert json.loads(entry.details)["rows"]["transactions"] == 50


def test_backfill_releases_several_days(store, source, tmp_path):
    result = run_simulation(store, source, str(tmp_path), days=3)
    assert result.dates == [date(2024, 5, d) for d in (1, 2, 3)]
    assert len(list((tmp_path / "transactions").iterdir())) == 3


def test_successive_runs_continue_from_the_stored_clock(store, source, tmp_path):
    run_simulation(store, source, str(tmp_path))
    result = run_simulation(store, source, str(tmp_path))
    assert result.dates == [date(2024, 5, 2)]


def test_run_crosses_into_the_synthetic_phase(store, source, tmp_path):
    store.clock = ClockState(virtual_date=date(2024, 5, 16))
    result = run_simulation(store, source, str(tmp_path), days=3)
    assert json.loads(store.run_log[0].details)["phases"] == ["replay", "synthetic"]
    assert result.clock.virtual_date == date(2024, 5, 19)


def test_scenarios_are_applied_and_logged(store, source, tmp_path):
    run_simulation(store, source, str(tmp_path), scenario_names=["null_spike"])
    text = (tmp_path / "transactions" / "date=2024-05-01" / "transactions.json").read_text()
    assert '"paymentMethod": null' in text
    assert json.loads(store.run_log[0].details)["scenarios"] == ["null_spike"]


def test_typo_in_scenarios_fails_before_any_work(store, source, tmp_path):
    with pytest.raises(ValueError, match="nope"):
        run_simulation(store, source, str(tmp_path), scenario_names=["nope"])
    assert store.clock is None
    assert not list(tmp_path.iterdir())


def test_failed_run_is_logged_and_leaves_the_clock_alone(store, source, tmp_path):
    store.clock = ClockState(virtual_date=date(2024, 5, 16))
    with pytest.raises(ValueError, match="no real data left"):
        run_simulation(store, source, str(tmp_path), days=3, mode="replay")
    assert store.clock.virtual_date == date(2024, 5, 16)
    [entry] = store.run_log
    assert entry.status == "failed"
    assert "no real data left" in json.loads(entry.details)["error"]


def test_rerunning_a_day_replaces_its_files(store, source, tmp_path):
    run_simulation(store, source, str(tmp_path), scenario_names=["duplicates"])
    store.clock = None
    run_simulation(store, source, str(tmp_path))
    lines = (tmp_path / "transactions" / "date=2024-05-01" / "transactions.json").read_text()
    assert len(lines.splitlines()) == 50


# ---- reset job ----


def _populate(tmp_path):
    raw, checkpoints = tmp_path / "raw", tmp_path / "checkpoints"
    (raw / "transactions" / "date=2024-05-01").mkdir(parents=True, exist_ok=True)
    (raw / "transactions" / "date=2024-05-01" / "transactions.json").write_text("{}\n")
    (checkpoints / "silver_sales" / "offsets").mkdir(parents=True, exist_ok=True)
    return raw, checkpoints


def _reset(store, tmp_path, **overrides):
    raw, checkpoints = (
        _populate(tmp_path)
        if overrides.pop("populate", True)
        else (
            tmp_path / "raw",
            tmp_path / "checkpoints",
        )
    )
    kwargs = dict(
        level="data",
        target="dev",
        confirm="dev",
        raw_dir=str(raw),
        checkpoints_dir=str(checkpoints),
        requested_by="tim",
    )
    kwargs.update(overrides)
    return run_reset(store, **kwargs), raw, checkpoints


def test_data_reset_clears_everything_but_keeps_folders_and_the_seed(store, tmp_path):
    store.clock = ClockState(virtual_date=date(2024, 5, 20), seed=7)
    store.log_run(
        RunLogEntry("r", "etl", "succeeded", datetime.now(), datetime.now(), None, "x", "{}")
    )
    _, raw, checkpoints = _reset(store, tmp_path)
    assert list(raw.iterdir()) == [] and raw.is_dir()
    assert list(checkpoints.iterdir()) == [] and checkpoints.is_dir()
    assert store.truncated == ["silver.customers", "silver.quarantine_sales", "gold.kpis"]
    assert store.run_log == []
    assert store.clock == ClockState.initial(seed=7)


def test_data_reset_leaves_parameters_alone(store, tmp_path):
    calls = []
    _reset(store, tmp_path, reseed_params=[lambda: calls.append(1)])
    assert calls == []
    assert "ops.app_metrics" not in store.truncated


def test_hard_reset_reseeds_parameters_and_clears_app_metrics(store, tmp_path):
    calls = []
    _reset(store, tmp_path, level="hard", reseed_params=[lambda: calls.append("a")])
    assert calls == ["a"]
    assert "ops.app_metrics" in store.truncated


def test_reset_works_when_nothing_exists_yet(store, tmp_path):
    result, _, _ = _reset(store, tmp_path, populate=False)
    assert store.clock == ClockState.initial()
    assert result.level == "data"


def test_reset_is_logged_and_the_log_survives_resets(store, tmp_path):
    _reset(store, tmp_path)
    _reset(store, tmp_path, level="hard")
    assert [(e.level, e.status, e.requested_by, e.target) for e in store.reset_log] == [
        ("data", "completed", "tim", "dev"),
        ("hard", "completed", "tim", "dev"),
    ]


def test_wrong_confirmation_refuses_and_touches_nothing(store, tmp_path):
    raw, _ = _populate(tmp_path)
    with pytest.raises(ValueError, match="confirm"):
        run_reset(
            store,
            level="data",
            target="dev",
            confirm="test",
            raw_dir=str(raw),
            checkpoints_dir=str(tmp_path / "checkpoints"),
        )
    assert (raw / "transactions").exists()
    assert store.reset_log == [] and store.truncated == []


def test_unknown_level_is_refused(store, tmp_path):
    with pytest.raises(ValueError, match="level"):
        _reset(store, tmp_path, level="everything")


def test_prod_hard_reset_needs_allow_prod(store, tmp_path):
    with pytest.raises(ValueError, match="allow_prod"):
        _reset(store, tmp_path, level="hard", target="prod", confirm="prod")
    assert store.reset_log == []
    _reset(store, tmp_path, level="hard", target="prod", confirm="prod", allow_prod=True)
    assert store.reset_log[0].target == "prod"


def test_prod_data_reset_does_not_need_allow_prod(store, tmp_path):
    _reset(store, tmp_path, target="prod", confirm="prod")
    assert store.reset_log[0].status == "completed"


def test_failed_reset_is_logged(store, tmp_path):
    def boom():
        raise RuntimeError("backend down")

    with pytest.raises(RuntimeError, match="backend down"):
        _reset(store, tmp_path, level="hard", reseed_params=[boom])
    [entry] = store.reset_log
    assert entry.status == "failed"
    assert "backend down" in entry.details


# ---- Spark store, with a stand-in for Spark ----


class _FakeSpark:
    def __init__(self, show_tables=None, existing=()):
        self.calls = []
        self._show = show_tables or {}
        self._existing = set(existing)
        self.catalog = type(
            "Catalog", (), {"tableExists": lambda _, name: name in self._existing}
        )()

    def sql(self, query, args=None):
        self.calls.append((query, args))
        rows = []
        if query.startswith("SHOW TABLES IN "):
            rows = [{"tableName": t} for t in self._show.get(query.rsplit(".", 1)[-1], [])]
        if "FROM" in query and "sim_clock" in query and query.startswith("SELECT"):
            rows = self._clock_rows if hasattr(self, "_clock_rows") else []
        return type("Result", (), {"collect": lambda _: rows})()


def test_spark_store_creates_the_three_ops_tables():
    spark = _FakeSpark()
    SparkOpsStore(spark, "cat").ensure_tables()
    queries = [q for q, _ in spark.calls]
    assert [q.split()[5] for q in queries] == [
        "cat.ops.sim_clock",
        "cat.ops.run_log",
        "cat.ops.reset_log",
    ]
    assert all(q.startswith("CREATE TABLE IF NOT EXISTS") for q in queries)


def test_spark_store_rejects_unsafe_identifiers():
    with pytest.raises(ValueError, match="identifier"):
        SparkOpsStore(_FakeSpark(), "cat; DROP TABLE x")


def test_spark_store_reads_and_writes_the_clock():
    spark = _FakeSpark()
    store = SparkOpsStore(spark, "cat")
    assert store.read_clock() is None
    spark._clock_rows = [(date(2024, 5, 3), 9)]
    assert store.read_clock() == ClockState(date(2024, 5, 3), 9)

    store.write_clock(ClockState(date(2024, 5, 4), 9))
    query, args = spark.calls[-1]
    assert query.startswith("INSERT OVERWRITE cat.ops.sim_clock")
    assert (args["vd"], args["seed"], args["phase"]) == ("2024-05-04", 9, "replay")


def test_spark_store_binds_values_instead_of_formatting_them():
    spark = _FakeSpark()
    SparkOpsStore(spark, "cat").log_run(
        RunLogEntry(
            "id'1",
            "simulator",
            "failed",
            datetime(2024, 1, 1),
            datetime(2024, 1, 2),
            None,
            "o'neil",
            "{}",
        )
    )
    query, args = spark.calls[-1]
    assert "o'neil" not in query and "id'1" not in query
    assert args["by"] == "o'neil" and args["vd"] is None


def test_spark_store_truncates_every_table_in_the_schemas():
    spark = _FakeSpark(show_tables={"silver": ["a", "b"], "gold": ["c"]})
    done = SparkOpsStore(spark, "cat").truncate_tables(["silver", "gold"])
    assert done == ["silver.a", "silver.b", "gold.c"]
    assert [q for q, _ in spark.calls if q.startswith("TRUNCATE")] == [
        "TRUNCATE TABLE cat.silver.a",
        "TRUNCATE TABLE cat.silver.b",
        "TRUNCATE TABLE cat.gold.c",
    ]


def test_spark_store_only_truncates_ops_tables_that_exist():
    spark = _FakeSpark(existing={"cat.ops.app_metrics"})
    store = SparkOpsStore(spark, "cat")
    assert store.truncate_ops_table("app_metrics") is True
    assert store.truncate_ops_table("missing") is False
    assert [q for q, _ in spark.calls] == ["TRUNCATE TABLE cat.ops.app_metrics"]
