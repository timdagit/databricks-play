"""The virtual clock and calendar (spec section 3.2).

The virtual calendar starts on 2024-05-01, the first day of the sample data. Replay maps each
real sample day onto the same date, so replay covers 1-17 May and synthetic days follow
from 2024-05-18.

`ClockState` is what `ops.sim_clock` holds. `virtual_date` is the last day released, and
"day 0" (nothing released yet) is the day before the calendar starts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

Phase = Literal["replay", "synthetic"]
Mode = Literal["auto", "replay", "synthetic"]

START_DATE = date(2024, 5, 1)
REPLAY_DAYS = 17
REPLAY_END = START_DATE + timedelta(days=REPLAY_DAYS)  # first synthetic day
DEFAULT_SEED = 42
MAX_DAYS_PER_RUN = 366


@dataclass(frozen=True)
class ClockState:
    virtual_date: date
    seed: int = DEFAULT_SEED

    @classmethod
    def initial(cls, seed: int = DEFAULT_SEED) -> ClockState:
        """Virtual day 0: nothing released yet."""
        return cls(virtual_date=START_DATE - timedelta(days=1), seed=seed)

    @property
    def days_released(self) -> int:
        return (self.virtual_date - START_DATE).days + 1

    @property
    def next_date(self) -> date:
        return self.virtual_date + timedelta(days=1)

    @property
    def phase(self) -> Phase:
        """The phase of the next day to be released."""
        return phase_for(self.next_date)


@dataclass(frozen=True)
class DayPlan:
    """One day the simulator is about to release."""

    virtual_date: date
    phase: Phase
    source_date: date | None  # the real sample day replayed, or None for a synthetic day


def phase_for(virtual_date: date) -> Phase:
    return "replay" if START_DATE <= virtual_date < REPLAY_END else "synthetic"


def plan_days(state: ClockState, days: int = 1, mode: Mode = "auto") -> list[DayPlan]:
    """The next `days` days to release, in order.

    - `auto`: replay while real days remain, then synthetic.
    - `replay`: only real days. Raises ValueError if they run out.
    - `synthetic`: every day is synthetic, even inside the replay window.
    """
    if mode not in ("auto", "replay", "synthetic"):
        raise ValueError(f"mode must be auto, replay or synthetic, got {mode!r}")
    if not 1 <= days <= MAX_DAYS_PER_RUN:
        raise ValueError(f"days must be between 1 and {MAX_DAYS_PER_RUN}, got {days}")

    plans = []
    for offset in range(days):
        day = state.next_date + timedelta(days=offset)
        in_replay_window = phase_for(day) == "replay"
        if mode == "replay" and not in_replay_window:
            raise ValueError(
                f"replay mode has no real data left for {day} (replay ends {REPLAY_END})"
            )
        replay = in_replay_window and mode != "synthetic"
        plans.append(DayPlan(day, "replay" if replay else "synthetic", day if replay else None))
    return plans


def advance(state: ClockState, plans: list[DayPlan]) -> ClockState:
    """The clock after the given plans have been released."""
    if not plans:
        return state
    return ClockState(virtual_date=plans[-1].virtual_date, seed=state.seed)
