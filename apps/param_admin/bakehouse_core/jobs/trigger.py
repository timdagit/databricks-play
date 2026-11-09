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


_RUNNING_STATES = {"PENDING", "QUEUED", "RUNNING", "TERMINATING", "BLOCKED", "WAITING_FOR_RETRY"}
_OK_RESULTS = {"SUCCESS", "SUCCESS_WITH_FAILURES"}


def run_state(life_cycle_state: str | None, result_state: str | None) -> RunState:
    """Collapse Databricks' run states into the three the app shows."""
    if life_cycle_state in _RUNNING_STATES:
        return "RUNNING"
    if life_cycle_state == "TERMINATED" and result_state in _OK_RESULTS:
        return "SUCCEEDED"
    return "FAILED"  # a failed, cancelled, skipped or errored run


def _name(value: object) -> str | None:
    """The text of an SDK enum (or plain string) value."""
    if value is None:
        return None
    return getattr(value, "value", None) or str(value)


class DatabricksJobTrigger(JobTrigger):
    """Starts and watches real jobs through the Databricks SDK.

    `client` is a `databricks.sdk.WorkspaceClient`. `job_ids` maps the app's job names (see
    `bakehouse_core.jobs.actions`) to job ids. On Databricks Apps those ids come from the app's
    job resources, so the app only has permission to run these jobs. The run executes as the
    job's own identity, not as the app.
    """

    def __init__(
        self,
        client: object,
        job_ids: Mapping[str, int],
        now_ms: Callable[[], float] | None = None,
    ):
        self._client = client
        self._job_ids = dict(job_ids)
        self._now_ms = now_ms or (lambda: time.time() * 1000)

    def _job_id(self, job_name: str) -> int:
        if job_name not in self._job_ids:
            raise KeyError(f"no job id configured for {job_name!r}")
        return self._job_ids[job_name]

    def _to_status(self, run: object, job_name: str) -> RunStatus:
        state = getattr(run, "state", None)
        lifecycle = _name(getattr(state, "life_cycle_state", None))
        result = _name(getattr(state, "result_state", None))
        started = getattr(run, "start_time", None)
        if getattr(run, "run_duration", None):  # set once the run has finished (milliseconds)
            seconds = run.run_duration / 1000
        elif started:
            seconds = max(0.0, (self._now_ms() - started) / 1000)
        else:
            seconds = 0.0
        return RunStatus(
            run_id=str(run.run_id),
            job_name=job_name,
            state=run_state(lifecycle, result),
            duration_seconds=seconds,
            url=getattr(run, "run_page_url", None),
        )

    def _job_name(self, job_id: int | None) -> str:
        for name, known in self._job_ids.items():
            if known == job_id:
                return name
        return str(job_id)

    def trigger(self, job_name: str, params: Mapping[str, str] | None = None) -> str:
        waiter = self._client.jobs.run_now(
            job_id=self._job_id(job_name), job_parameters=dict(params or {})
        )
        return str(waiter.response.run_id)

    def status(self, run_id: str) -> RunStatus:
        from databricks.sdk.errors import NotFound

        try:
            run = self._client.jobs.get_run(run_id=int(run_id))
        except NotFound:
            raise UnknownRunError(run_id) from None
        return self._to_status(run, self._job_name(getattr(run, "job_id", None)))

    def active_run(self, job_name: str) -> RunStatus | None:
        runs = self._client.jobs.list_runs(job_id=self._job_id(job_name), active_only=True, limit=1)
        for run in runs:
            return self._to_status(run, job_name)
        return None
