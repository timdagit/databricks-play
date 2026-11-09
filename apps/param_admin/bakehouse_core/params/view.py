"""What the parameter screen shows and does, without any web framework.

The Dash app calls these functions and only arranges the results on the page.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from bakehouse_core.params import model
from bakehouse_core.params.repository import ParamRepository, ParamValidationError
from bakehouse_core.params.schema import SEED_EFFECTIVE_FROM

FROM_THE_START = "from the start"


def display_date(value: date) -> str:
    """The seed date reads as "from the start". Real dates are shown as they are."""
    return FROM_THE_START if value == SEED_EFFECTIVE_FROM else value.isoformat()


def build_rows(repo: ParamRepository) -> list[dict[str, Any]]:
    """One row per parameter, in definition order, with its current value and version."""
    rows = []
    for d in model.DEFINITIONS:
        latest = repo.history(d.key)[-1]
        rows.append(
            {
                "key": d.key,
                "value": model.to_text(latest.value),
                "range": f"{model.to_text(d.min_value)} to {model.to_text(d.max_value)}",
                "unit": d.unit,
                "effective_from": display_date(latest.effective_from),
                "version": latest.version,
                "updated_by": latest.updated_by,
                "description": d.description,
            }
        )
    return rows


def changed_values(original: list[dict[str, Any]], edited: list[dict[str, Any]]) -> dict[str, str]:
    """Keys whose value text differs between the loaded rows and the edited table."""
    before = {r["key"]: str(r["value"]).strip() for r in original}
    changes = {}
    for row in edited:
        text = str(row["value"]).strip()
        if before.get(row["key"]) != text:
            changes[row["key"]] = text
    return changes


def parse_effective_from(text: str | None) -> date | None:
    """A date typed as YYYY-MM-DD, or None if left blank."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError("use the form YYYY-MM-DD") from None


@dataclass(frozen=True)
class SaveResult:
    ok: bool
    message: str
    errors: dict[str, str]
    versions: dict[str, int]


def save_changes(
    repo: ParamRepository,
    changes: dict[str, str],
    user: str,
    note: str = "",
    effective_from_text: str | None = None,
) -> SaveResult:
    if not changes:
        return SaveResult(False, "Nothing has changed.", {}, {})
    try:
        effective_from = parse_effective_from(effective_from_text)
    except ValueError as exc:
        return SaveResult(False, "Fix the effective-from date.", {"effective_from": str(exc)}, {})
    # Blank means every day. The ETL reads parameters as of the virtual date, which the app
    # cannot see, and a real "today" is meaningless in simulated time.
    effective_from = effective_from or SEED_EFFECTIVE_FROM
    try:
        versions = repo.save(changes, user=user, note=note, effective_from=effective_from)
    except ParamValidationError as exc:
        return SaveResult(False, "Nothing was saved. Fix the values below.", exc.errors, {})
    names = ", ".join(f"{k} (v{v})" for k, v in versions.items())
    return SaveResult(True, f"Saved {names}.", {}, versions)


def history_rows(repo: ParamRepository, key: str) -> list[dict[str, Any]]:
    """Every version of one parameter, newest first."""
    return [
        {
            "version": h.version,
            "value": model.to_text(h.value),
            "effective_from": display_date(h.effective_from),
            "updated_by": h.updated_by,
            "updated_at": h.updated_at.strftime("%Y-%m-%d %H:%M"),
            "note": h.change_note,
        }
        for h in reversed(repo.history(key))
    ]


def merge_pending(pending: dict | None, effective_from_text: str | None) -> dict:
    """Remember which days saved changes could affect, until the next rerun.

    `pending` is `None` (nothing saved yet) or `{"from": "YYYY-MM-DD" | None}`, where a missing
    date means every day. A blank effective-from date means every day. Several saves keep the
    earliest date, so one rerun covers all of them.
    """
    new = parse_effective_from(effective_from_text)
    if pending is None:
        return {"from": new.isoformat() if new else None}
    old = pending.get("from")
    if old is None or new is None:
        return {"from": None}
    return {"from": min(old, new.isoformat())}


def rerun_from_date(pending: dict | None) -> date | None:
    """The first day a rerun has to rebuild, or None for every day (also when unsure)."""
    if not pending or not pending.get("from"):
        return None
    return date.fromisoformat(pending["from"])
