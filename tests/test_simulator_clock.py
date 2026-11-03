from datetime import date

import pytest

from bakehouse_core.simulator import clock
from bakehouse_core.simulator.clock import ClockState


def test_initial_state_is_day_zero():
    state = ClockState.initial()
    assert state.virtual_date == date(2024, 4, 30)
    assert state.days_released == 0
    assert state.next_date == date(2024, 5, 1)
    assert state.phase == "replay"


def test_replay_covers_seventeen_days_then_synthetic():
    assert clock.phase_for(date(2024, 5, 1)) == "replay"
    assert clock.phase_for(date(2024, 5, 17)) == "replay"
    assert clock.phase_for(date(2024, 5, 18)) == "synthetic"


def test_dates_before_the_calendar_are_not_replay():
    assert clock.phase_for(date(2024, 4, 30)) == "synthetic"


def test_plan_one_day_from_day_zero():
    [plan] = clock.plan_days(ClockState.initial())
    assert plan == clock.DayPlan(date(2024, 5, 1), "replay", date(2024, 5, 1))


def test_plan_crosses_from_replay_into_synthetic():
    state = ClockState(virtual_date=date(2024, 5, 16))
    plans = clock.plan_days(state, days=3)
    assert [p.virtual_date for p in plans] == [
        date(2024, 5, 17),
        date(2024, 5, 18),
        date(2024, 5, 19),
    ]
    assert [p.phase for p in plans] == ["replay", "synthetic", "synthetic"]
    assert [p.source_date for p in plans] == [date(2024, 5, 17), None, None]


def test_synthetic_mode_overrides_the_replay_window():
    plans = clock.plan_days(ClockState.initial(), days=2, mode="synthetic")
    assert [p.phase for p in plans] == ["synthetic", "synthetic"]
    assert [p.source_date for p in plans] == [None, None]


def test_replay_mode_refuses_to_run_past_the_real_data():
    state = ClockState(virtual_date=date(2024, 5, 16))
    assert len(clock.plan_days(state, days=1, mode="replay")) == 1
    with pytest.raises(ValueError, match="no real data left"):
        clock.plan_days(state, days=2, mode="replay")


@pytest.mark.parametrize("days", [0, -1, 367])
def test_days_out_of_range(days):
    with pytest.raises(ValueError, match="days"):
        clock.plan_days(ClockState.initial(), days=days)


def test_unknown_mode():
    with pytest.raises(ValueError, match="mode"):
        clock.plan_days(ClockState.initial(), mode="fast")


def test_advance_moves_to_the_last_planned_day_and_keeps_the_seed():
    state = ClockState.initial(seed=7)
    plans = clock.plan_days(state, days=5)
    after = clock.advance(state, plans)
    assert after.virtual_date == date(2024, 5, 5)
    assert after.days_released == 5
    assert after.seed == 7
    assert after.next_date == date(2024, 5, 6)


def test_advance_with_no_plans_changes_nothing():
    state = ClockState.initial()
    assert clock.advance(state, []) == state


def test_running_one_day_at_a_time_matches_a_backfill():
    state = ClockState.initial()
    for _ in range(20):
        state = clock.advance(state, clock.plan_days(state))
    assert state == clock.advance(ClockState.initial(), clock.plan_days(ClockState.initial(), 20))
