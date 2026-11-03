"""Delta implementation of `OpsStore`, run with Spark SQL on serverless compute.

Values are bound as query parameters. Catalog, schema and table names cannot be bound, so they
are checked against a strict pattern before going into the SQL text.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

from bakehouse_core.jobs.store import OpsStore, ResetLogEntry, RunLogEntry
from bakehouse_core.simulator.clock import ClockState

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ident(name: str) -> str:
    if not _IDENTIFIER.match(name):
        raise ValueError(f"not a valid identifier: {name!r}")
    return name


class SparkOpsStore(OpsStore):
    def __init__(self, spark: Any, catalog: str):
        self._spark = spark
        self._catalog = _ident(catalog)

    def _t(self, schema: str, table: str) -> str:
        return f"{self._catalog}.{_ident(schema)}.{_ident(table)}"

    def _sql(self, query: str, **args: Any) -> Any:
        return self._spark.sql(query, args=args) if args else self._spark.sql(query)

    def ensure_tables(self) -> None:
        self._sql(
            f"CREATE TABLE IF NOT EXISTS {self._t('ops', 'sim_clock')} "
            "(virtual_date DATE, seed INT, phase STRING, updated_at TIMESTAMP)"
        )
        self._sql(
            f"CREATE TABLE IF NOT EXISTS {self._t('ops', 'run_log')} "
            "(run_id STRING, job STRING, status STRING, started_at TIMESTAMP, "
            "finished_at TIMESTAMP, virtual_date DATE, triggered_by STRING, details STRING)"
        )
        self._sql(
            f"CREATE TABLE IF NOT EXISTS {self._t('ops', 'reset_log')} "
            "(reset_id STRING, reset_at TIMESTAMP, level STRING, target STRING, "
            "requested_by STRING, status STRING, details STRING)"
        )

    def read_clock(self) -> ClockState | None:
        rows = self._sql(f"SELECT virtual_date, seed FROM {self._t('ops', 'sim_clock')}").collect()
        if not rows:
            return None
        virtual_date, seed = rows[0][0], rows[0][1]
        return ClockState(virtual_date=virtual_date, seed=int(seed))

    def write_clock(self, state: ClockState) -> None:
        self._sql(
            f"INSERT OVERWRITE {self._t('ops', 'sim_clock')} "
            "SELECT CAST(:vd AS DATE), CAST(:seed AS INT), :phase, CAST(:now AS TIMESTAMP)",
            vd=state.virtual_date.isoformat(),
            seed=state.seed,
            phase=state.phase,
            now=datetime.now(UTC).isoformat(),
        )

    def log_run(self, entry: RunLogEntry) -> None:
        self._sql(
            f"INSERT INTO {self._t('ops', 'run_log')} "
            "SELECT :run_id, :job, :status, CAST(:started AS TIMESTAMP), "
            "CAST(:finished AS TIMESTAMP), CAST(:vd AS DATE), :by, :details",
            run_id=entry.run_id,
            job=entry.job,
            status=entry.status,
            started=entry.started_at.isoformat(),
            finished=entry.finished_at.isoformat(),
            vd=_iso_or_none(entry.virtual_date),
            by=entry.triggered_by,
            details=entry.details,
        )

    def log_reset(self, entry: ResetLogEntry) -> None:
        self._sql(
            f"INSERT INTO {self._t('ops', 'reset_log')} "
            "SELECT :reset_id, CAST(:at AS TIMESTAMP), :level, :target, :by, :status, :details",
            reset_id=entry.reset_id,
            at=entry.reset_at.isoformat(),
            level=entry.level,
            target=entry.target,
            by=entry.requested_by,
            status=entry.status,
            details=entry.details,
        )

    def clear_run_log(self) -> None:
        self._sql(f"TRUNCATE TABLE {self._t('ops', 'run_log')}")

    def truncate_tables(self, schemas: list[str]) -> list[str]:
        done = []
        for schema in schemas:
            for row in self._sql(f"SHOW TABLES IN {self._catalog}.{_ident(schema)}").collect():
                name = row["tableName"]
                self._sql(f"TRUNCATE TABLE {self._t(schema, name)}")
                done.append(f"{schema}.{name}")
        return done

    def truncate_ops_table(self, name: str) -> bool:
        full = self._t("ops", name)
        if not self._spark.catalog.tableExists(full):
            return False
        self._sql(f"TRUNCATE TABLE {full}")
        return True


def _iso_or_none(value: date | None) -> str | None:
    return value.isoformat() if value else None
