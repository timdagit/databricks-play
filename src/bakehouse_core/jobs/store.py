"""The operational tables the jobs share: the clock, the run log and the reset log.

`OpsStore` is the interface the job logic uses. `InMemoryOpsStore` backs the local tests and
`bakehouse_core.io.ops_tables.SparkOpsStore` backs the real jobs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime

from bakehouse_core.simulator.clock import ClockState


@dataclass(frozen=True)
class RunLogEntry:
    run_id: str
    job: str  # "simulator" or "etl"
    status: str  # "succeeded" or "failed"
    started_at: datetime
    finished_at: datetime
    virtual_date: date | None
    triggered_by: str
    details: str  # JSON text: what the run did, parameter versions used, errors


@dataclass(frozen=True)
class ResetLogEntry:
    reset_id: str
    reset_at: datetime
    level: str
    target: str
    requested_by: str
    status: str  # "completed" or "failed"
    details: str  # JSON text


class OpsStore(ABC):
    @abstractmethod
    def ensure_tables(self) -> None:
        """Create the tables if they are missing. Safe to call every run."""

    @abstractmethod
    def read_clock(self) -> ClockState | None:
        """The stored clock, or None if it has never been written."""

    @abstractmethod
    def write_clock(self, state: ClockState) -> None: ...

    @abstractmethod
    def log_run(self, entry: RunLogEntry) -> None: ...

    @abstractmethod
    def log_reset(self, entry: ResetLogEntry) -> None: ...

    @abstractmethod
    def clear_run_log(self) -> None: ...

    @abstractmethod
    def truncate_tables(self, schemas: list[str]) -> list[str]:
        """Empty every table in the given schemas (keeping the tables). Returns their names."""

    @abstractmethod
    def truncate_ops_table(self, name: str) -> bool:
        """Empty one `ops` table if it exists. Returns whether it existed."""


class InMemoryOpsStore(OpsStore):
    def __init__(
        self, tables: dict[str, list[str]] | None = None, ops_tables: set[str] | None = None
    ):
        self.clock: ClockState | None = None
        self.run_log: list[RunLogEntry] = []
        self.reset_log: list[ResetLogEntry] = []
        self.tables = tables or {}  # schema -> table names
        self.ops_tables = ops_tables or set()
        self.truncated: list[str] = []
        self.ensured = False

    def ensure_tables(self) -> None:
        self.ensured = True

    def read_clock(self) -> ClockState | None:
        return self.clock

    def write_clock(self, state: ClockState) -> None:
        self.clock = state

    def log_run(self, entry: RunLogEntry) -> None:
        self.run_log.append(entry)

    def log_reset(self, entry: ResetLogEntry) -> None:
        self.reset_log.append(entry)

    def clear_run_log(self) -> None:
        self.run_log.clear()

    def truncate_tables(self, schemas: list[str]) -> list[str]:
        names = [f"{s}.{t}" for s in schemas for t in self.tables.get(s, [])]
        self.truncated += names
        return names

    def truncate_ops_table(self, name: str) -> bool:
        if name in self.ops_tables:
            self.truncated.append(f"ops.{name}")
            return True
        return False
