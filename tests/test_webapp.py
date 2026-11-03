import json

import pytest
import sqlalchemy as sa

from bakehouse_core.config.idle import IdleTracker
from bakehouse_core.jobs.trigger import FakeJobTrigger
from bakehouse_core.params import model
from bakehouse_core.params.repository import SqlRepository
from bakehouse_core.webapp import Handlers, create_app


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def parts():
    repo = SqlRepository(sa.create_engine("sqlite://"))
    repo.ensure_schema()
    clock = Clock()
    trigger = FakeJobTrigger(run_seconds=10, clock=clock)
    tracker = IdleTracker(clock)
    return repo, trigger, tracker, clock


@pytest.fixture
def handlers(parts):
    repo, trigger, tracker, _ = parts
    return Handlers(repo, trigger, tracker)


def edited(rows, key, value):
    return [{**r, "value": value} if r["key"] == key else dict(r) for r in rows]


# --- handlers ----------------------------------------------------------------------------


def test_first_load_shows_the_defaults(handlers):
    update = handlers.table_action(None, None, None, None, None, "tim")
    assert [r["key"] for r in update.rows] == [d.key for d in model.DEFINITIONS]
    assert update.rows == update.original
    assert update.message == ""


def test_save_stores_a_new_version_and_reloads_the_rows(handlers, parts):
    loaded = handlers.table_action(None, None, None, None, None, "tim").rows
    update = handlers.table_action(
        "save", edited(loaded, "underperform_pct", "0.30"), loaded, "2024-05-10", "stricter", "tim"
    )
    assert update.ok
    row = next(r for r in update.rows if r["key"] == "underperform_pct")
    assert (row["value"], row["version"], row["updated_by"]) == ("0.30", 2, "tim")
    assert update.original == update.rows
    assert parts[0].history("underperform_pct")[-1].change_note == "stricter"


def test_invalid_save_keeps_the_edits_and_lists_the_errors(handlers, parts):
    loaded = handlers.table_action(None, None, None, None, None, "tim").rows
    bad = edited(loaded, "underperform_pct", "0.9")
    update = handlers.table_action("save", bad, loaded, None, None, "tim")
    assert update.ok is False
    assert "underperform_pct" in update.errors
    assert update.rows == bad  # the user's text stays in the table
    assert update.original == loaded
    assert parts[0].load_current() == model.defaults()


def test_saving_without_changes_says_so(handlers):
    loaded = handlers.table_action(None, None, None, None, None, "tim").rows
    update = handlers.table_action("save", loaded, loaded, None, None, "tim")
    assert update.ok is False
    assert update.message == "Nothing has changed."


def test_history_for_the_selected_row(handlers, parts):
    parts[0].save({"review_alert_min_count": 5}, user="a")
    rows = handlers.table_action(None, None, None, None, None, "tim").rows
    index = [r["key"] for r in rows].index("review_alert_min_count")
    title, history = handlers.history(rows, [index])
    assert title == "Versions of review_alert_min_count"
    assert [h["version"] for h in history] == [2, 1]
    assert handlers.history(rows, [])[1] == []


def test_run_buttons_start_a_job_and_block_a_second(handlers, parts):
    result, tracked = handlers.run("rerun_etl", "tim@example.com")
    assert result.started
    assert tracked == {"run_id": "fake-run-1", "label": "Rerun ETL"}
    second, tracked2 = handlers.run("next_day", "tim@example.com")
    assert not second.started
    assert tracked2 is None


def test_poll_reports_progress_and_disables_buttons_while_running(handlers, parts):
    clock = parts[3]
    _, tracked = handlers.run("next_day", "tim")
    clock.now = 4
    running = handlers.poll(tracked)
    assert running.buttons_disabled
    assert running.text == "Run next day: running (4s)"
    clock.now = 10
    done = handlers.poll(tracked)
    assert not done.buttons_disabled
    assert done.text == "Run next day: succeeded (10s)"
    assert handlers.poll(None).text == "No run in progress."


def test_polling_is_not_activity_but_user_actions_are(handlers, parts):
    tracker, clock = parts[2], parts[3]
    clock.now = 100
    handlers.poll(None)
    assert tracker.idle_seconds() == 100
    handlers.run("next_day", "tim")
    assert tracker.idle_seconds() == 0
    clock.now = 130
    handlers.table_action("reload", None, None, None, None, "tim")
    assert tracker.idle_seconds() == 0
    clock.now = 150
    handlers.table_action(None, None, None, None, None, "tim")  # page load, not a user action
    assert tracker.idle_seconds() == 20


# --- the real Flask app --------------------------------------------------------------------


@pytest.fixture
def client(parts):
    repo, trigger, tracker, _ = parts
    app = create_app(
        repo, trigger, tracker=tracker, backend_name="sqlite", resolve_user=lambda: "tim"
    )
    return app.server.test_client()


def test_page_and_layout_are_served(client):
    assert client.get("/").status_code == 200
    layout = client.get("/_dash-layout").get_json()
    assert "Bakehouse parameters" in json.dumps(layout)


def test_idle_endpoint_reports_disabled_locally(client):
    body = client.get("/idle").get_json()
    assert body["enabled"] is False
    assert body["idle_seconds"] >= 0


def test_poll_callback_round_trip(client):
    payload = {
        "output": "..run-status.children...rerun_etl.disabled...next_day.disabled..",
        "outputs": [
            {"id": "run-status", "property": "children"},
            {"id": "rerun_etl", "property": "disabled"},
            {"id": "next_day", "property": "disabled"},
        ],
        "inputs": [
            {"id": "poll", "property": "n_intervals", "value": 1},
            {"id": "tracked-run", "property": "data", "value": None},
        ],
        "changedPropIds": ["poll.n_intervals"],
    }
    response = client.post("/_dash-update-component", json=payload)
    assert response.status_code == 200
    body = response.get_json()["response"]
    assert body["run-status"]["children"] == "No run in progress."
    assert body["rerun_etl"]["disabled"] is False
