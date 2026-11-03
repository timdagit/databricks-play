"""The parameter admin page (spec section 4.4) as a Dash app.

`Handlers` holds what each callback does, with plain arguments, so it is tested without a
browser. `create_app` arranges the page and connects the callbacks to the handlers. It takes the
repository, the job trigger and the idle tracker as arguments, so the same page runs on
Postgres, Lakebase or Delta, and with a fake or a real job trigger.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from dash import Dash, Input, Output, State, ctx, dash_table, dcc, html
from flask import jsonify, request

from bakehouse_core.config.identity import user_from_headers
from bakehouse_core.config.idle import IdleTracker
from bakehouse_core.jobs import actions
from bakehouse_core.jobs.trigger import JobTrigger, UnknownRunError
from bakehouse_core.params import view
from bakehouse_core.params.repository import ParamRepository

POLL_MILLISECONDS = 4000

COLUMNS = [
    {"name": "Parameter", "id": "key", "editable": False},
    {"name": "Value", "id": "value", "editable": True},
    {"name": "Allowed range", "id": "range", "editable": False},
    {"name": "Unit", "id": "unit", "editable": False},
    {"name": "Effective from", "id": "effective_from", "editable": False},
    {"name": "Version", "id": "version", "editable": False},
    {"name": "Updated by", "id": "updated_by", "editable": False},
]
HISTORY_COLUMNS = [
    {"name": "Version", "id": "version"},
    {"name": "Value", "id": "value"},
    {"name": "Effective from", "id": "effective_from"},
    {"name": "Updated by", "id": "updated_by"},
    {"name": "Saved at", "id": "updated_at"},
    {"name": "Note", "id": "note"},
]


@dataclass(frozen=True)
class TableUpdate:
    rows: list[dict[str, Any]]
    original: list[dict[str, Any]]
    message: str
    errors: dict[str, str] = field(default_factory=dict)
    ok: bool | None = None  # None: just loaded
    pending: dict | None = None  # days the saves since the last rerun could affect


@dataclass(frozen=True)
class Poll:
    buttons_disabled: bool
    text: str


class Handlers:
    def __init__(self, repo: ParamRepository, trigger: JobTrigger, tracker: IdleTracker):
        self.repo = repo
        self.trigger = trigger
        self.tracker = tracker

    def table_action(
        self,
        which: str | None,
        edited: list[dict[str, Any]] | None,
        original: list[dict[str, Any]] | None,
        effective_from: str | None,
        note: str | None,
        user: str,
        pending: dict | None = None,
    ) -> TableUpdate:
        """Load or reload (`which` is None or `reload`), or save the edits (`save`)."""
        if which != "save" or not edited or original is None:
            if which is not None:
                self.tracker.touch()
            rows = view.build_rows(self.repo)
            return TableUpdate(
                rows, rows, "Loaded the current values." if which else "", pending=pending
            )
        self.tracker.touch()
        result = view.save_changes(
            self.repo, view.changed_values(original, edited), user, note or "", effective_from
        )
        if result.ok:
            rows = view.build_rows(self.repo)
            merged = view.merge_pending(pending, effective_from)
            return TableUpdate(rows, rows, result.message, ok=True, pending=merged)
        return TableUpdate(
            edited, original, result.message, result.errors, ok=False, pending=pending
        )

    def history(self, rows: list[dict[str, Any]] | None, selected: list[int] | None):
        if not rows or not selected:
            return "Select a parameter to see its versions.", []
        self.tracker.touch()
        key = rows[selected[0]]["key"]
        return f"Versions of {key}", view.history_rows(self.repo, key)

    def run(
        self, action_key: str, user: str, pending: dict | None = None
    ) -> tuple[actions.StartResult, dict | None, dict | None]:
        """Start a run. Returns the result, what to remember for the status line, and the
        pending-days note afterwards (cleared once a rerun has started)."""
        self.tracker.touch()
        from_date = view.rerun_from_date(pending) if action_key == "rerun_etl" else None
        result = actions.start_action(self.trigger, action_key, user, from_date)
        tracked = None
        if result.started and result.run_id:
            tracked = {"run_id": result.run_id, "label": actions.ACTIONS[action_key].label}
            if action_key == "rerun_etl":
                pending = None
        return result, tracked, pending

    def poll(self, tracked: dict | None) -> Poll:
        """Status for the page. Never touches the idle tracker: polling is not activity."""
        parts = []
        tracked_id = None
        if tracked:
            try:
                status = self.trigger.status(tracked["run_id"])
                tracked_id = tracked["run_id"]
                parts.append(actions.describe_run(status, tracked["label"]))
            except UnknownRunError:
                # The server restarted, or the run is gone: say so instead of failing.
                parts.append(f"{tracked['label']}: no longer known to the server")
        active = actions.any_active(self.trigger)
        if active is not None and active.run_id != tracked_id:
            parts.append(actions.describe_run(active))
        return Poll(active is not None, " | ".join(parts) or "No run in progress.")


def _message(update: TableUpdate) -> list:
    if not update.message:
        return []
    cls = {True: "ok", False: "error", None: "info"}[update.ok]
    children: list = [html.Div(update.message, className=cls)]
    if update.errors:
        children.append(
            html.Ul(
                [html.Li(f"{key}: {text}") for key, text in update.errors.items()], className=cls
            )
        )
    return children


def create_app(
    repo: ParamRepository,
    trigger: JobTrigger,
    *,
    tracker: IdleTracker | None = None,
    default_user: str = "local-user",
    backend_name: str = "",
    idle_enabled: bool = False,
    assets_folder: str = "assets",
    resolve_user: Callable[[], str] | None = None,
) -> Dash:
    tracker = tracker or IdleTracker()
    handlers = Handlers(repo, trigger, tracker)
    app = Dash(__name__, assets_folder=assets_folder, title="Bakehouse parameters")
    user_of = resolve_user or (lambda: user_from_headers(request.headers, default_user))

    app.layout = html.Div(
        className="page",
        children=[
            html.H1("Bakehouse parameters"),
            html.P(f"Backend: {backend_name or 'unknown'}", className="muted"),
            html.Div(id="message"),
            dash_table.DataTable(
                id="params",
                columns=COLUMNS,
                data=[],
                editable=True,
                row_selectable="single",
                tooltip_delay=0,
                tooltip_duration=None,
                style_cell={"textAlign": "left", "padding": "6px"},
                style_header={"fontWeight": "bold"},
            ),
            html.Div(
                className="controls",
                children=[
                    dcc.Input(
                        id="effective-from",
                        type="text",
                        placeholder="Effective from (YYYY-MM-DD, blank = from the start)",
                        debounce=True,
                    ),
                    dcc.Input(id="note", type="text", placeholder="Note", debounce=True),
                    html.Button("Save", id="save", n_clicks=0),
                    html.Button("Reload", id="reload", n_clicks=0),
                ],
            ),
            html.H2("History", id="history-title"),
            dash_table.DataTable(id="history", columns=HISTORY_COLUMNS, data=[]),
            html.H2("Apply the change"),
            html.P(
                "Rerun ETL rebuilds gold with the saved values. Run next day releases one more "
                "virtual day and processes it.",
                className="muted",
            ),
            html.Div(
                className="controls",
                children=[
                    html.Button("Rerun ETL", id="rerun_etl", n_clicks=0),
                    html.Button("Run next day", id="next_day", n_clicks=0),
                ],
            ),
            html.Div(id="run-message"),
            html.Div(id="run-status", className="muted"),
            dcc.Store(id="original"),
            dcc.Store(id="pending"),
            dcc.Store(id="tracked-run"),
            dcc.Interval(id="poll", interval=POLL_MILLISECONDS),
        ],
    )

    @app.callback(
        Output("params", "data"),
        Output("params", "tooltip_data"),
        Output("original", "data"),
        Output("message", "children"),
        Output("pending", "data"),
        Input("save", "n_clicks"),
        Input("reload", "n_clicks"),
        State("params", "data"),
        State("original", "data"),
        State("effective-from", "value"),
        State("note", "value"),
        State("pending", "data"),
    )
    def on_table(_save, _reload, edited, original, effective_from, note, pending):
        which = {"save": "save", "reload": "reload"}.get(ctx.triggered_id)
        update = handlers.table_action(
            which, edited, original, effective_from, note, user_of(), pending
        )
        tooltips = [{"key": {"value": row["description"], "type": "text"}} for row in update.rows]
        return update.rows, tooltips, update.original, _message(update), update.pending

    @app.callback(
        Output("history-title", "children"),
        Output("history", "data"),
        Input("params", "selected_rows"),
        Input("original", "data"),
        State("params", "data"),
    )
    def on_history(selected, _original, rows):
        return handlers.history(rows, selected)

    @app.callback(
        Output("run-message", "children"),
        Output("tracked-run", "data"),
        Output("pending", "data", allow_duplicate=True),
        Input("rerun_etl", "n_clicks"),
        Input("next_day", "n_clicks"),
        State("pending", "data"),
        prevent_initial_call=True,
    )
    def on_run(_rerun, _next, pending):
        if ctx.triggered_id not in actions.ACTIONS:
            return [], None, pending
        result, tracked, pending = handlers.run(ctx.triggered_id, user_of(), pending)
        cls = "ok" if result.started else "error"
        return html.Div(result.message, className=cls), tracked, pending

    @app.callback(
        Output("run-status", "children"),
        Output("rerun_etl", "disabled"),
        Output("next_day", "disabled"),
        Input("poll", "n_intervals"),
        Input("tracked-run", "data"),
    )
    def on_poll(_n, tracked):
        poll = handlers.poll(tracked)
        return poll.text, poll.buttons_disabled, poll.buttons_disabled

    @app.server.route("/idle")
    def idle():
        return jsonify(enabled=idle_enabled, idle_seconds=round(tracker.idle_seconds(), 1))

    return app
