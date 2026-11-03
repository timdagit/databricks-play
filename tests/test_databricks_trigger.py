from types import SimpleNamespace

import pytest

from bakehouse_core.jobs.trigger import (
    DatabricksJobTrigger,
    UnknownRunError,
    run_state,
)

JOBS = {"bakehouse_etl": 11, "daily_run": 22}


def run(run_id=5, job_id=11, lifecycle="RUNNING", result=None, start=0, duration=None, url="u"):
    return SimpleNamespace(
        run_id=run_id,
        job_id=job_id,
        state=SimpleNamespace(life_cycle_state=lifecycle, result_state=result),
        start_time=start,
        run_duration=duration,
        run_page_url=url,
    )


class FakeJobsApi:
    def __init__(self, get=None, listed=()):
        self.run_now_calls = []
        self._get = get
        self._listed = list(listed)
        self.list_calls = []

    def run_now(self, job_id, job_parameters):
        self.run_now_calls.append((job_id, job_parameters))
        return SimpleNamespace(response=SimpleNamespace(run_id=987))

    def get_run(self, run_id):
        if isinstance(self._get, Exception):
            raise self._get
        return self._get

    def list_runs(self, job_id, active_only, limit):
        self.list_calls.append((job_id, active_only, limit))
        return iter(self._listed)


def trigger_with(jobs_api, now_ms=0):
    client = SimpleNamespace(jobs=jobs_api)
    return DatabricksJobTrigger(client, JOBS, now_ms=lambda: now_ms)


@pytest.mark.parametrize(
    ("lifecycle", "result", "expected"),
    [
        ("PENDING", None, "RUNNING"),
        ("QUEUED", None, "RUNNING"),
        ("RUNNING", None, "RUNNING"),
        ("TERMINATING", None, "RUNNING"),
        ("TERMINATED", "SUCCESS", "SUCCEEDED"),
        ("TERMINATED", "SUCCESS_WITH_FAILURES", "SUCCEEDED"),
        ("TERMINATED", "FAILED", "FAILED"),
        ("TERMINATED", "CANCELED", "FAILED"),
        ("TERMINATED", "TIMEDOUT", "FAILED"),
        ("SKIPPED", None, "FAILED"),
        ("INTERNAL_ERROR", None, "FAILED"),
    ],
)
def test_run_states_are_collapsed_to_three(lifecycle, result, expected):
    assert run_state(lifecycle, result) == expected


def test_trigger_starts_the_job_with_parameters_and_returns_the_run_id():
    api = FakeJobsApi()
    run_id = trigger_with(api).trigger("daily_run", {"days": "1", "triggered_by": "me"})
    assert run_id == "987"
    assert api.run_now_calls == [(22, {"days": "1", "triggered_by": "me"})]


def test_an_unconfigured_job_is_an_error():
    with pytest.raises(KeyError, match="no job id"):
        trigger_with(FakeJobsApi()).trigger("nope")


def test_status_of_a_running_run_counts_time_since_it_started():
    api = FakeJobsApi(get=run(lifecycle="RUNNING", start=10_000))
    status = trigger_with(api, now_ms=70_000).status("5")
    assert (status.state, status.duration_seconds, status.job_name) == (
        "RUNNING",
        60.0,
        "bakehouse_etl",
    )
    assert status.url == "u"


def test_status_of_a_finished_run_uses_its_recorded_duration():
    api = FakeJobsApi(get=run(lifecycle="TERMINATED", result="SUCCESS", duration=125_000))
    status = trigger_with(api, now_ms=10**12).status("5")
    assert (status.state, status.duration_seconds) == ("SUCCEEDED", 125.0)


def test_sdk_enums_are_read_by_their_value():
    state = SimpleNamespace(value="TERMINATED")
    result = SimpleNamespace(value="SUCCESS")
    api = FakeJobsApi(get=run(lifecycle=state, result=result))
    assert trigger_with(api).status("5").state == "SUCCEEDED"


def test_a_run_that_no_longer_exists_is_unknown():
    from databricks.sdk.errors import NotFound

    api = FakeJobsApi(get=NotFound("gone"))
    with pytest.raises(UnknownRunError):
        trigger_with(api).status("5")


def test_active_run_asks_for_active_runs_of_that_job_only():
    api = FakeJobsApi(listed=[run(run_id=8, job_id=22, lifecycle="RUNNING", start=0)])
    status = trigger_with(api, now_ms=5000).active_run("daily_run")
    assert api.list_calls == [(22, True, 1)]
    assert (status.run_id, status.job_name, status.state) == ("8", "daily_run", "RUNNING")


def test_no_active_run_is_none():
    assert trigger_with(FakeJobsApi(listed=[])).active_run("daily_run") is None
