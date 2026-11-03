"""Write a generated day as JSON Lines files (spec section 3.2).

Layout: `{entity}/date=YYYY-MM-DD/{entity}.json`, one JSON object per line, which is what
Auto Loader's `json` format reads. A re-run of the same day writes the same file names with the
same content, so the layout is idempotent.

On serverless compute a Unity Catalog volume is an ordinary folder at
`/Volumes/<catalog>/<schema>/<volume>`, so plain file writes are enough and no Spark is needed.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from bakehouse_core.simulator.generate import ENTITIES, Row


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"cannot serialise {type(value).__name__}")


def to_json_lines(rows: list[Row]) -> str:
    return "".join(json.dumps(r, default=_json_default, ensure_ascii=False) + "\n" for r in rows)


def relative_path(entity: str, virtual_date: date) -> str:
    return f"{entity}/date={virtual_date.isoformat()}/{entity}.json"


def day_files(day: dict[str, list[Row]], virtual_date: date) -> dict[str, str]:
    """Relative path to file content for every entity that has rows. Empty entities are skipped."""
    unknown = set(day) - set(ENTITIES)
    if unknown:
        raise ValueError(f"unknown entities: {', '.join(sorted(unknown))}")
    return {
        relative_path(entity, virtual_date): to_json_lines(rows)
        for entity, rows in day.items()
        if rows
    }


def write_day(day: dict[str, list[Row]], virtual_date: date, base_dir: str | Path) -> list[Path]:
    """Write the day under `base_dir` (e.g. the raw volume). Returns the files written."""
    base = Path(base_dir)
    written = []
    for rel, content in day_files(day, virtual_date).items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def delete_day(virtual_date: date, base_dir: str | Path) -> None:
    """Remove a day's files, so a day can be regenerated cleanly."""
    base = Path(base_dir)
    for entity in ENTITIES:
        folder = base / entity / f"date={virtual_date.isoformat()}"
        if folder.is_dir():
            for f in folder.iterdir():
                f.unlink()
            folder.rmdir()
