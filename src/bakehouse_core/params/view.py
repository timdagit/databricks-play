"""What the parameter screen shows and does, without any web framework.

The Dash app calls these functions and only arranges the results on the page.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from bakehouse_core.params import model
from bakehouse_core.params.repository import ParamRepository, ParamValidationError


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
                "effective_from": latest.effective_from.isoformat(),
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
    """A date typed as YYYY-MM-DD. Empty means today (None)."""
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
            "effective_from": h.effective_from.isoformat(),
            "updated_by": h.updated_by,
            "updated_at": h.updated_at.strftime("%Y-%m-%d %H:%M"),
            "note": h.change_note,
        }
        for h in reversed(repo.history(key))
    ]
