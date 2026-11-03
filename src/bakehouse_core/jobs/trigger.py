"""Starting jobs from the app, behind an interface so the app runs locally without Databricks."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

RunState = Literal["RUNNING", "SUCCEEDED", "FAILED"]


@dataclass(frozen=True)
class RunStatus:
    run_id: str
    job_name: str
    state: RunState
    duration_seconds: float
    url: str | None = None  # a link to the run page, when there is one


class UnknownRunError(KeyError):
    """The run id is not known to the trigger, e.g. after the server restarted."""


class JobTrigger(ABC):
    @abstractmethod
    def trigger(self, job_name: str, params: Mapping[str, str] | None = None) -> str:
        """Start a job run and return a run id. Does not wait for it to finish."""

    @abstractmethod
    def status(self, run_id: str) -> RunStatus:
        """The current state of a run started earlier. Raises `UnknownRunError` if unknown."""

    @abstractmethod
    def active_run(self, job_name: str) -> RunStatus | None:
        """The run of `job_name` that is in progress, if any."""


@dataclass
class FakeJobTrigger(JobTrigger):
    """Simulates runs for local UI work and tests. Nothing really runs.

    A run reports RUNNING until `run_seconds` have passed on `clock`, then ends in
    `final_state`.
    """

    run_seconds: float = 8.0
    final_state: RunState = "SUCCEEDED"
    clock: Callable[[], float] = time.monotonic
    calls: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    _started: dict[str, tuple[str, float]] = field(default_factory=dict)

    def trigger(self, job_name: str, params: Mapping[str, str] | None = None) -> str:
        self.calls.append((job_name, dict(params or {})))
        run_id = f"fake-run-{len(self.calls)}"
        self._started[run_id] = (job_name, self.clock())
        return run_id

    def status(self, run_id: str) -> RunStatus:
        if run_id not in self._started:
            raise UnknownRunError(run_id)
        job_name, started = self._started[run_id]
        elapsed = self.clock() - started
        state: RunState = "RUNNING" if elapsed < self.run_seconds else self.final_state
        return RunStatus(run_id, job_name, state, min(elapsed, self.run_seconds))

    def active_run(self, job_name: str) -> RunStatus | None:
        for run_id, (name, _started) in self._started.items():
            if name == job_name:
                status = self.status(run_id)
                if status.state == "RUNNING":
                    return status
        return None
