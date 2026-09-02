"""The chore scheduling engine -- dates and turn-taking."""

from __future__ import annotations

import datetime as dt

import pytest

from app.rotation import (
    add_months,
    catch_up,
    describe_cadence,
    next_due,
    rotate_assignee,
    rotate_back,
    status_for,
    step_once,
)

D = dt.date


class TestAddMonths:
    def test_simple_shift(self):
        assert add_months(D(2026, 3, 15), 1) == D(2026, 4, 15)

    def test_clamps_to_short_month(self):
        # Jan 31 + 1 month has nowhere to land but the end of February.
        assert add_months(D(2026, 1, 31), 1) == D(2026, 2, 28)

    def test_clamps_to_leap_february(self):
        assert add_months(D(2028, 1, 31), 1) == D(2028, 2, 29)

    def test_crosses_year_boundary(self):
        assert add_months(D(2026, 11, 30), 2) == D(2027, 1, 30)

    def test_december_rollover(self):
        assert add_months(D(2026, 12, 31), 1) == D(2027, 1, 31)


class TestStepOnce:
    def test_daily(self):
        assert step_once(D(2026, 8, 10), "daily") == D(2026, 8, 11)

    def test_weekly_with_interval(self):
        assert step_once(D(2026, 8, 10), "weekly", 2) == D(2026, 8, 24)

    def test_monthly(self):
        assert step_once(D(2026, 8, 10), "monthly") == D(2026, 9, 10)

    def test_once_does_not_repeat(self):
        with pytest.raises(ValueError):
            step_once(D(2026, 8, 10), "once")


class TestNextDue:
    def test_on_time_completion_advances_one_period(self):
        assert next_due(D(2026, 8, 10), "weekly", 1, D(2026, 8, 10)) == D(2026, 8, 17)

    def test_one_off_returns_none(self):
        assert next_due(D(2026, 8, 10), "once", 1, D(2026, 8, 10)) is None

    def test_neglected_chore_catches_up_past_today(self):
        """A chore ignored for a month shouldn't reappear as still overdue."""
        upcoming = next_due(D(2026, 7, 1), "weekly", 1, D(2026, 8, 10))
        assert upcoming > D(2026, 8, 10)
        assert upcoming == D(2026, 8, 12)

    def test_early_completion_keeps_the_original_rhythm(self):
        # Done two days early: the next one is still a week after the due date,
        # not a week after today, so a weekly chore stays on its weekday.
        assert next_due(D(2026, 8, 10), "weekly", 1, D(2026, 8, 8)) == D(2026, 8, 17)

    def test_daily_chore_weeks_behind_lands_tomorrow(self):
        assert next_due(D(2026, 7, 20), "daily", 1, D(2026, 8, 10)) == D(2026, 8, 11)


class TestCatchUp:
    """Missed chores rejoin the schedule instead of stacking up as debt."""

    today = D(2026, 8, 10)

    def test_a_daily_chore_weeks_behind_is_simply_due_today(self):
        assert catch_up(D(2026, 7, 20), "daily", 1, self.today) == self.today

    def test_a_weekly_chore_keeps_its_weekday(self):
        # Due on a Tuesday in July; today is the following Monday. It lands on
        # the most recent Tuesday, not on today.
        assert catch_up(D(2026, 7, 7), "weekly", 1, D(2026, 8, 10)) == D(2026, 8, 4)

    def test_never_lands_in_the_future(self):
        for start in (D(2026, 6, 1), D(2026, 7, 15), D(2026, 8, 9)):
            for cadence in ("daily", "weekly", "monthly"):
                assert catch_up(start, cadence, 1, self.today) <= self.today

    def test_lateness_never_exceeds_one_period(self):
        landed = catch_up(D(2026, 5, 4), "weekly", 1, self.today)
        assert (self.today - landed).days < 7

    def test_respects_the_interval(self):
        # Every two weeks from June 1: the 15th, the 29th, July 13th...
        assert catch_up(D(2026, 6, 1), "weekly", 2, self.today) == D(2026, 8, 10)

    def test_a_chore_due_today_is_left_alone(self):
        assert catch_up(self.today, "daily", 1, self.today) == self.today

    def test_a_future_chore_is_not_dragged_back(self):
        """Snoozing pushes a due date forward; catching up must not undo it."""
        assert catch_up(D(2026, 8, 20), "daily", 1, self.today) == D(2026, 8, 20)

    def test_one_offs_stay_as_late_as_they_really_are(self):
        assert catch_up(D(2026, 6, 1), "once", 1, self.today) == D(2026, 6, 1)

    def test_a_chore_one_day_late_stays_one_day_late(self):
        """Yesterday's weekly bins are still owed today, not deferred a week."""
        assert catch_up(D(2026, 8, 9), "weekly", 1, self.today) == D(2026, 8, 9)

    def test_monthly_clamping_does_not_run_away(self):
        landed = catch_up(D(2026, 1, 31), "monthly", 1, D(2026, 4, 15))
        assert landed == D(2026, 3, 28)

    def test_is_idempotent(self):
        once = catch_up(D(2026, 6, 3), "weekly", 1, self.today)
        assert catch_up(once, "weekly", 1, self.today) == once


class TestRotate:
    ring = [1, 2, 3]

    def test_advances_to_next(self):
        assert rotate_assignee(self.ring, 1) == 2
        assert rotate_assignee(self.ring, 2) == 3

    def test_wraps_around(self):
        assert rotate_assignee(self.ring, 3) == 1

    def test_unknown_holder_restarts_at_front(self):
        # Happens when the assignee moves out or the ring is edited.
        assert rotate_assignee(self.ring, 99) == 1

    def test_none_holder_starts_at_front(self):
        assert rotate_assignee(self.ring, None) == 1

    def test_empty_ring(self):
        assert rotate_assignee([], 1) is None

    def test_single_person_keeps_it(self):
        assert rotate_assignee([7], 7) == 7


class TestRotateBack:
    ring = [1, 2, 3]

    def test_steps_backwards(self):
        assert rotate_back(self.ring, 2) == 1
        assert rotate_back(self.ring, 3) == 2

    def test_wraps_around(self):
        assert rotate_back(self.ring, 1) == 3

    def test_undoes_a_forward_rotation_exactly(self):
        for member in self.ring:
            assert rotate_back(self.ring, rotate_assignee(self.ring, member)) == member

    def test_unknown_holder_restarts_at_front(self):
        assert rotate_back(self.ring, 99) == 1

    def test_empty_ring(self):
        assert rotate_back([], 1) is None

    def test_single_person_keeps_it(self):
        assert rotate_back([7], 7) == 7


class TestStatus:
    today = D(2026, 8, 10)

    def test_buckets(self):
        assert status_for(D(2026, 8, 9), self.today) == "overdue"
        assert status_for(D(2026, 8, 10), self.today) == "today"
        assert status_for(D(2026, 8, 12), self.today) == "soon"
        assert status_for(D(2026, 8, 30), self.today) == "later"


def test_cadence_labels():
    assert describe_cadence("weekly", 1) == "every week"
    assert describe_cadence("weekly", 2) == "every 2 weeks"
    assert describe_cadence("once", 1) == "one-off"
