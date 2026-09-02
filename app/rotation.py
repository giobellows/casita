"""Chore scheduling: when is it next due, and whose turn is it.

Pure functions over plain values -- no database, no request context -- because
this is the part with the fiddly edge cases (month-end arithmetic, chores that
went ignored for a month, rotations whose members moved out) and it deserves
tests that run in microseconds.
"""

from __future__ import annotations

import datetime as dt

CADENCES = ("daily", "weekly", "monthly", "once")
ROTATION_MODES = ("anyone", "fixed", "rotate")

# A chore left undone for years shouldn't spin forever when we roll it forward.
_MAX_CATCHUP_STEPS = 500


def add_months(day: dt.date, months: int) -> dt.date:
    """Shift by whole months, clamping to the end of short months.

    Jan 31 + 1 month is Feb 28 (or 29), not a crash and not March 3. The clamp
    means a "last day of the month" chore drifts to the 28th rather than
    marching backwards, which is the trade every calendar library makes.
    """
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    # Days in the target month, found without importing calendar.
    if month == 12:
        first_of_next = dt.date(year + 1, 1, 1)
    else:
        first_of_next = dt.date(year, month + 1, 1)
    last_day = (first_of_next - dt.timedelta(days=1)).day
    return dt.date(year, month, min(day.day, last_day))


def step_once(due_on: dt.date, cadence: str, interval_n: int = 1) -> dt.date:
    """Advance a due date by exactly one cadence period."""
    n = max(1, interval_n)
    if cadence == "daily":
        return due_on + dt.timedelta(days=n)
    if cadence == "weekly":
        return due_on + dt.timedelta(weeks=n)
    if cadence == "monthly":
        return add_months(due_on, n)
    raise ValueError(f"cadence {cadence!r} does not repeat")


def next_due(
    due_on: dt.date, cadence: str, interval_n: int, today: dt.date
) -> dt.date | None:
    """Where a chore lands after being completed.

    Returns None for one-off chores, which are simply finished.

    A chore that fell three weeks behind rolls forward past today rather than
    landing in the past -- otherwise you'd tick it off and watch it reappear as
    overdue, which reads like the app calling you a liar.
    """
    if cadence == "once":
        return None

    upcoming = step_once(due_on, cadence, interval_n)
    steps = 0
    while upcoming <= today and steps < _MAX_CATCHUP_STEPS:
        upcoming = step_once(upcoming, cadence, interval_n)
        steps += 1
    return upcoming


def catch_up(
    due_on: dt.date, cadence: str, interval_n: int, today: dt.date
) -> dt.date:
    """Roll a missed chore forward into its current period.

    Where `next_due` lands *past* today because the chore was just done, this
    stops *at* today because it wasn't: the chore is still owed, it just stops
    accumulating a lateness figure that grows without bound. A daily chore
    nobody got to for three weeks comes back as due today rather than "19 days
    late"; a weekly one comes back due this week, at most one period behind.

    Chores are a schedule, not a debt. Missing Tuesday's bins doesn't mean you
    owe two bin-days on Wednesday, and a list that opens as a wall of red is one
    people stop opening.

    One-offs don't repeat, so they keep their date and stay as late as they
    genuinely are -- there's no next occurrence for "fix the door" to roll into.
    """
    if cadence == "once" or due_on >= today:
        return due_on

    current = due_on
    steps = 0
    while steps < _MAX_CATCHUP_STEPS:
        following = step_once(current, cadence, interval_n)
        if following > today:
            break
        current = following
        steps += 1
    return current


def rotate_assignee(
    rotation_member_ids: list[int], current_assignee_id: int | None
) -> int | None:
    """Hand the baton to the next roommate in the ring.

    Falls back to the front of the ring when the current holder isn't in it --
    which happens when someone moves out, or the rotation was just edited.
    """
    if not rotation_member_ids:
        return None
    if current_assignee_id not in rotation_member_ids:
        return rotation_member_ids[0]
    position = rotation_member_ids.index(current_assignee_id)
    return rotation_member_ids[(position + 1) % len(rotation_member_ids)]


def rotate_back(
    rotation_member_ids: list[int], current_assignee_id: int | None
) -> int | None:
    """Hand the baton back to the previous roommate -- the inverse of
    `rotate_assignee`, used when a completion is undone.

    Round-trips exactly: rotate_back(rotate_assignee(ring, x)) == x for any x
    in the ring. If the ring was edited in between, this lands on the front
    rather than guessing, same as rotating forwards.
    """
    if not rotation_member_ids:
        return None
    if current_assignee_id not in rotation_member_ids:
        return rotation_member_ids[0]
    position = rotation_member_ids.index(current_assignee_id)
    return rotation_member_ids[(position - 1) % len(rotation_member_ids)]


def status_for(due_on: dt.date, today: dt.date) -> str:
    """Bucket a due date for display: overdue / today / soon / later."""
    delta = (due_on - today).days
    if delta < 0:
        return "overdue"
    if delta == 0:
        return "today"
    if delta <= 3:
        return "soon"
    return "later"


def describe_cadence(cadence: str, interval_n: int) -> str:
    """Human phrasing for a schedule, e.g. 'every 2 weeks'."""
    n = max(1, interval_n)
    if cadence == "once":
        return "one-off"
    unit = {"daily": "day", "weekly": "week", "monthly": "month"}[cadence]
    if n == 1:
        return {"daily": "every day", "weekly": "every week", "monthly": "every month"}[
            cadence
        ]
    return f"every {n} {unit}s"
