"""The parameters one ETL run used, as the text stored in `ops.run_log.details`.

`load_params` writes it and the later tasks read it back, so every task in a run uses the same
values and the log shows exactly which parameter versions were in force.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date

from bakehouse_core.params import model


def to_json(
    as_of: date,
    values: Mapping[str, model.Value],
    versions: Mapping[str, int],
    defaults_used: bool = False,
) -> str:
    """`defaults_used` marks a run whose parameters were the seed defaults, not a saved store."""
    data: dict[str, object] = {
        "as_of": as_of.isoformat(),
        "versions": dict(versions),
        "values": {k: model.to_text(v) for k, v in values.items()},
    }
    if defaults_used:
        data["defaults_used"] = True
    return json.dumps(data, sort_keys=True)


def values_from_details(details: str) -> dict[str, model.Value]:
    """Typed parameter values from a `load_params` log entry's details."""
    raw = json.loads(details)["values"]
    return {k: model.from_text(k, text) for k, text in raw.items()}
