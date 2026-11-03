"""The two run buttons in the app (spec section 4.4): what each starts and with which values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from bakehouse_core.jobs.trigger import JobTrigger, RunStatus


@dataclass(frozen=True)
class Action:
    label: str
    job_name: str
    params: dict[str, str]


ACTIONS = {
    # Rebuild gold with the saved parameters. Silver does not change.
    "rerun_etl": Action("Rerun ETL", "bakehouse_etl", {"mode": "recompute_gold"}),
    # Release one more virtual day and process it.
    "next_day": Action("Run next day", "daily_run", {"days": "1"}),
}


@dataclass(frozen=True)
class StartResult:
    started: bool
    message: str
    run_id: str | None = None
    active: RunStatus | None = None


def any_active(trigger: JobTrigger) -> RunStatus | None:
    """A run of either job in progress. The two jobs share tables, so only one may run."""
    for action in ACTIONS.values():
        status = trigger.active_run(action.job_name)
        if status is not None:
            return status
    return None


def start_action(
    trigger: JobTrigger, action_key: str, user: str, from_date: date | None = None
) -> StartResult:
    """Start the action's job unless one is already running. `user` goes to `triggered_by`.

    `from_date` limits Rerun ETL to the days from a changed parameter's effective date.
    """
    action = ACTIONS.get(action_key)
    if action is None:
        raise ValueError(f"unknown action: {action_key}")
    active = any_active(trigger)
    if active is not None:
        return StartResult(
            False, f"A run of {active.job_name} is already in progress.", None, active
        )
    params = {**action.params, "triggered_by": user}
    if from_date is not None and action_key == "rerun_etl":
        params["from_date"] = from_date.isoformat()
    run_id = trigger.trigger(action.job_name, params)
    return StartResult(True, f"Started {action.label}.", run_id)


def describe_run(status: RunStatus, label: str | None = None) -> str:
    """One line for the status panel, e.g. `Rerun ETL: succeeded (8s)`."""
    name = label or status.job_name
    return f"{name}: {status.state.lower()} ({status.duration_seconds:.0f}s)"
