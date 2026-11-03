from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from bakehouse_core.config.idle import IdleTracker
from bakehouse_core.jobs import actions
from bakehouse_core.jobs.trigger import FakeJobTrigger
from bakehouse_core.params import model, view
from bakehouse_core.params.repository import SqlRepository
from bakehouse_core.params.schema import init_schema


@pytest.fixture
def repo():
    engine = sa.create_engine("sqlite://")
    init_schema(engine)
    return SqlRepository(engine)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


# --- the parameter screen ---------------------------------------------------------------


def test_rows_follow_definition_order_and_show_the_current_version(repo):
    repo.save({"underperform_pct": "0.30"}, user="tim", effective_from=date(2024, 5, 10))
    rows = view.build_rows(repo)
    assert [r["key"] for r in rows] == [d.key for d in model.DEFINITIONS]
    row = next(r for r in rows if r["key"] == "underperform_pct")
    assert row["value"] == "0.30"
    assert row["version"] == 2
    assert row["effective_from"] == "2024-05-10"
    assert row["updated_by"] == "tim"
    assert row["range"] == "0.05 to 0.50"


def test_changed_values_only_lists_edited_rows(repo):
    original = view.build_rows(repo)
    edited = [dict(r) for r in original]
    edited[0]["value"] = " 300 "
    edited[1]["value"] = edited[1]["value"]
    assert view.changed_values(original, edited) == {edited[0]["key"]: "300"}


def test_save_changes_saves_and_reports_versions(repo):
    result = view.save_changes(repo, {"underperform_pct": "0.30"}, "tim", "stricter", "2024-05-10")
    assert result.ok
    assert result.versions == {"underperform_pct": 2}
    assert "underperform_pct (v2)" in result.message
    assert repo.history("underperform_pct")[-1].change_note == "stricter"


def test_save_changes_reports_errors_and_saves_nothing(repo):
    result = view.save_changes(
        repo, {"underperform_pct": "0.9", "review_alert_min_count": "5"}, "t"
    )
    assert not result.ok
    assert "underperform_pct" in result.errors
    assert repo.load_current() == model.defaults()


def test_save_changes_with_no_changes_does_nothing(repo):
    result = view.save_changes(repo, {}, "tim")
    assert not result.ok
    assert result.message == "Nothing has changed."


def test_bad_effective_date_is_reported_against_the_date_field(repo):
    result = view.save_changes(repo, {"underperform_pct": "0.30"}, "tim", "", "10/05/2024")
    assert not result.ok
    assert result.errors == {"effective_from": "use the form YYYY-MM-DD"}
    assert repo.load_current() == model.defaults()


def test_blank_effective_date_in_a_save_means_from_the_start(repo):
    result = view.save_changes(repo, {"underperform_pct": "0.30"}, "tim")
    assert result.ok
    saved = repo.history("underperform_pct")[-1]
    assert saved.effective_from == date(1900, 1, 1)
    # Whatever virtual day the ETL asks about, the saved value is in force.
    assert repo.load_as_of(date(2024, 5, 1))["underperform_pct"] == Decimal("0.30")


def test_the_seed_date_is_shown_as_from_the_start(repo):
    rows = view.build_rows(repo)
    assert {r["effective_from"] for r in rows} == {"from the start"}
    assert view.history_rows(repo, "underperform_pct")[0]["effective_from"] == "from the start"
    repo.save({"underperform_pct": "0.30"}, user="tim", effective_from=date(2024, 5, 10))
    assert view.history_rows(repo, "underperform_pct")[0]["effective_from"] == "2024-05-10"


def test_empty_effective_date_parses_to_none(repo):
    assert view.parse_effective_from("") is None
    assert view.parse_effective_from(None) is None
    assert view.parse_effective_from(" 2024-05-10 ") == date(2024, 5, 10)


def test_history_is_newest_first(repo):
    repo.save({"review_alert_min_count": 5}, user="a", effective_from=date(2024, 5, 2))
    repo.save({"review_alert_min_count": 7}, user="b", effective_from=date(2024, 5, 3))
    rows = view.history_rows(repo, "review_alert_min_count")
    assert [r["version"] for r in rows] == [3, 2, 1]
    assert [r["value"] for r in rows] == ["7", "5", "3"]
    assert rows[0]["updated_by"] == "b"


# --- the run buttons --------------------------------------------------------------------


def test_actions_start_the_right_jobs_with_the_user():
    trigger = FakeJobTrigger()
    result = actions.start_action(trigger, "rerun_etl", "tim@example.com")
    assert result.started
    assert trigger.calls == [
        ("bakehouse_etl", {"mode": "recompute_gold", "triggered_by": "tim@example.com"})
    ]
    result = actions.start_action(FakeJobTrigger(), "next_day", "tim@example.com")
    assert result.started


def test_next_day_asks_for_one_day():
    trigger = FakeJobTrigger()
    actions.start_action(trigger, "next_day", "u")
    assert trigger.calls == [("daily_run", {"days": "1", "triggered_by": "u"})]


def test_a_second_run_is_refused_while_one_is_active():
    clock = Clock()
    trigger = FakeJobTrigger(run_seconds=10, clock=clock)
    assert actions.start_action(trigger, "next_day", "u").started
    clock.now = 3
    result = actions.start_action(trigger, "rerun_etl", "u")  # a different job: still refused
    assert not result.started
    assert result.active.job_name == "daily_run"
    assert len(trigger.calls) == 1


def test_a_new_run_is_allowed_once_the_last_one_finished():
    clock = Clock()
    trigger = FakeJobTrigger(run_seconds=10, clock=clock)
    actions.start_action(trigger, "next_day", "u")
    clock.now = 10
    assert actions.start_action(trigger, "rerun_etl", "u").started


def test_unknown_action_is_rejected():
    with pytest.raises(ValueError):
        actions.start_action(FakeJobTrigger(), "nope", "u")


def test_fake_run_status_progresses_and_can_fail():
    clock = Clock()
    trigger = FakeJobTrigger(run_seconds=5, final_state="FAILED", clock=clock)
    run_id = trigger.trigger("daily_run")
    clock.now = 2
    assert trigger.status(run_id).state == "RUNNING"
    assert trigger.status(run_id).duration_seconds == 2
    clock.now = 9
    assert trigger.status(run_id).state == "FAILED"
    assert trigger.status(run_id).duration_seconds == 5
    assert trigger.active_run("daily_run") is None


# --- the idle tracker ---------------------------------------------------------------------


def test_idle_time_grows_until_touched():
    clock = Clock()
    tracker = IdleTracker(clock)
    clock.now = 90
    assert tracker.idle_seconds() == 90
    tracker.touch()
    assert tracker.idle_seconds() == 0
    clock.now = 100
    assert tracker.idle_seconds() == 10


def test_rerun_can_be_limited_to_days_from_a_date():
    trigger = FakeJobTrigger()
    actions.start_action(trigger, "rerun_etl", "u", from_date=date(2024, 5, 10))
    assert trigger.calls[0][1] == {
        "mode": "recompute_gold",
        "triggered_by": "u",
        "from_date": "2024-05-10",
    }


def test_from_date_is_only_passed_to_rerun_etl():
    trigger = FakeJobTrigger()
    actions.start_action(trigger, "next_day", "u", from_date=date(2024, 5, 10))
    assert "from_date" not in trigger.calls[0][1]


def test_pending_days_merge_rules():
    assert view.merge_pending(None, "2024-05-10") == {"from": "2024-05-10"}
    assert view.merge_pending(None, "") == {"from": None}
    assert view.merge_pending({"from": "2024-05-10"}, "2024-05-12") == {"from": "2024-05-10"}
    assert view.merge_pending({"from": "2024-05-10"}, "2024-05-03") == {"from": "2024-05-03"}
    assert view.merge_pending({"from": None}, "2024-05-03") == {"from": None}
    assert view.merge_pending({"from": "2024-05-10"}, None) == {"from": None}


def test_rerun_from_date():
    assert view.rerun_from_date(None) is None
    assert view.rerun_from_date({"from": None}) is None
    assert view.rerun_from_date({"from": "2024-05-10"}) == date(2024, 5, 10)
