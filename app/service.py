"""Business operations over the database.

The API layer stays thin: it parses input, calls in here, and returns the result.
Anything involving more than one table, or any derived field the frontend needs,
lives in this module.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import config, models, money, rotation, schemas


def today() -> dt.date:
    """Today in the house's timezone, not the server's."""
    return dt.datetime.now(config.HOUSE_TZ).date()


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------


def member_out(member: models.Member | None) -> schemas.MemberOut | None:
    if member is None:
        return None
    return schemas.MemberOut(
        id=member.id,
        name=member.name,
        emoji=member.emoji,
        color=member.color,
        active=member.active,
    )


def chore_out(chore: models.Chore, on: dt.date) -> schemas.ChoreOut:
    ring = [slot.member for slot in chore.rotation if slot.member is not None]

    next_up: models.Member | None = None
    if chore.rotation_mode == "rotate" and ring:
        upcoming_id = rotation.rotate_assignee(
            [m.id for m in ring], chore.assignee_id
        )
        next_up = next((m for m in ring if m.id == upcoming_id), None)

    return schemas.ChoreOut(
        id=chore.id,
        name=chore.name,
        emoji=chore.emoji,
        notes=chore.notes,
        cadence=chore.cadence,
        interval_n=chore.interval_n,
        cadence_label=rotation.describe_cadence(chore.cadence, chore.interval_n),
        due_on=chore.due_on,
        status=rotation.status_for(chore.due_on, on),
        days_until=(chore.due_on - on).days,
        rotation_mode=chore.rotation_mode,
        assignee=member_out(chore.assignee),
        next_up=member_out(next_up),
        rotation=[member_out(m) for m in ring],  # type: ignore[misc]
        archived=chore.archived,
    )


# --------------------------------------------------------------------------
# Members
# --------------------------------------------------------------------------


def list_members(session: Session, include_inactive: bool = False):
    stmt = select(models.Member).order_by(
        models.Member.sort_order, models.Member.id
    )
    if not include_inactive:
        stmt = stmt.where(models.Member.active.is_(True))
    return list(session.scalars(stmt))


def create_member(session: Session, data: schemas.MemberIn) -> models.Member:
    highest = session.scalar(select(models.Member.sort_order).order_by(
        models.Member.sort_order.desc()
    ))
    member = models.Member(
        name=data.name.strip(),
        emoji=data.emoji or "🙂",
        color=data.color or "violet",
        sort_order=(highest or 0) + 1,
    )
    session.add(member)
    session.commit()
    return member


def update_member(
    session: Session, member: models.Member, data: schemas.MemberIn
) -> models.Member:
    member.name = data.name.strip()
    member.emoji = data.emoji or "🙂"
    member.color = data.color or "violet"
    session.commit()
    return member


def deactivate_member(session: Session, member: models.Member) -> None:
    """Soft delete. Past chores and expenses still reference them, and history
    that silently rewrites itself is worse than a greyed-out name."""
    member.active = False
    session.commit()


# --------------------------------------------------------------------------
# Chores
# --------------------------------------------------------------------------


def list_chores(session: Session, include_archived: bool = False):
    stmt = select(models.Chore).order_by(
        models.Chore.due_on, models.Chore.sort_order, models.Chore.id
    )
    if not include_archived:
        stmt = stmt.where(models.Chore.archived.is_(False))
    return list(session.scalars(stmt))


def _apply_rotation(
    session: Session, chore: models.Chore, member_ids: list[int]
) -> None:
    """Replace a chore's rotation ring with the given ordered members."""
    session.execute(
        delete(models.ChoreRotationSlot).where(
            models.ChoreRotationSlot.chore_id == chore.id
        )
    )
    session.flush()
    seen: set[int] = set()
    position = 0
    for member_id in member_ids:
        if member_id in seen:
            continue
        seen.add(member_id)
        session.add(
            models.ChoreRotationSlot(
                chore_id=chore.id, member_id=member_id, position=position
            )
        )
        position += 1


def create_chore(session: Session, data: schemas.ChoreIn) -> models.Chore:
    chore = models.Chore(
        name=data.name.strip(),
        emoji=data.emoji or "🧹",
        notes=data.notes,
        cadence=data.cadence,
        interval_n=data.interval_n,
        due_on=data.due_on or today(),
        rotation_mode=data.rotation_mode,
        assignee_id=data.assignee_id,
    )
    session.add(chore)
    session.flush()

    if data.rotation_mode == "rotate":
        _apply_rotation(session, chore, data.rotation_member_ids)
        # Kick the ring off at the first seat unless a starter was named.
        if chore.assignee_id is None and data.rotation_member_ids:
            chore.assignee_id = data.rotation_member_ids[0]
    elif data.rotation_mode == "anyone":
        chore.assignee_id = None

    session.commit()
    session.refresh(chore)
    return chore


def update_chore(
    session: Session, chore: models.Chore, data: schemas.ChoreIn
) -> models.Chore:
    chore.name = data.name.strip()
    chore.emoji = data.emoji or "🧹"
    chore.notes = data.notes
    chore.cadence = data.cadence
    chore.interval_n = data.interval_n
    if data.due_on is not None:
        chore.due_on = data.due_on
    chore.rotation_mode = data.rotation_mode

    if data.rotation_mode == "rotate":
        _apply_rotation(session, chore, data.rotation_member_ids)
        candidates = data.rotation_member_ids
        # If the current holder just left the ring, start over at the front.
        if chore.assignee_id not in candidates:
            chore.assignee_id = candidates[0] if candidates else None
    elif data.rotation_mode == "fixed":
        chore.assignee_id = data.assignee_id
    else:
        chore.assignee_id = None

    session.commit()
    session.refresh(chore)
    return chore


def complete_chore(
    session: Session, chore: models.Chore, member_id: int | None
) -> models.Chore:
    """Log the completion, roll the date forward, pass the baton."""
    now = today()
    session.add(
        models.ChoreCompletion(
            chore_id=chore.id, member_id=member_id, due_on=chore.due_on
        )
    )

    upcoming = rotation.next_due(chore.due_on, chore.cadence, chore.interval_n, now)
    if upcoming is None:
        # A one-off is simply finished; archiving keeps it out of the live list
        # without destroying the record of who did it.
        chore.archived = True
    else:
        chore.due_on = upcoming
        if chore.rotation_mode == "rotate":
            ring = [slot.member_id for slot in chore.rotation]
            chore.assignee_id = rotation.rotate_assignee(ring, chore.assignee_id)

    session.commit()
    session.refresh(chore)
    return chore


def undo_last_completion(session: Session, chore: models.Chore) -> models.Chore:
    """Put a chore back the way it was before it was last ticked off.

    Completing a chore changes exactly three things -- the due date, the
    rotation's current holder, and (for one-offs) the archived flag -- so undo
    reverses those three and deletes the audit row, which also removes it from
    the monthly recap.

    The old due date isn't recomputed, it's read back off the completion record,
    which stored it at the time. That matters because rolling a date forward
    isn't reversible arithmetic: a neglected chore catches up past today, so
    stepping backwards by one period would land somewhere it never was.

    Raises LookupError if the chore has never been completed.
    """
    last = session.scalars(
        select(models.ChoreCompletion)
        .where(models.ChoreCompletion.chore_id == chore.id)
        .order_by(
            models.ChoreCompletion.completed_at.desc(),
            models.ChoreCompletion.id.desc(),
        )
        .limit(1)
    ).first()

    if last is None:
        raise LookupError("That chore hasn't been completed yet")

    chore.due_on = last.due_on
    if chore.cadence == "once":
        chore.archived = False
    if chore.rotation_mode == "rotate":
        ring = [slot.member_id for slot in chore.rotation]
        chore.assignee_id = rotation.rotate_back(ring, chore.assignee_id)

    session.delete(last)
    session.commit()
    session.refresh(chore)
    return chore


def snooze_chore(session: Session, chore: models.Chore, days: int) -> models.Chore:
    chore.due_on = chore.due_on + dt.timedelta(days=days)
    session.commit()
    session.refresh(chore)
    return chore


def reassign_chore(
    session: Session, chore: models.Chore, member_id: int | None
) -> models.Chore:
    chore.assignee_id = member_id
    if member_id is not None and chore.rotation_mode == "anyone":
        # Claiming an unassigned chore implicitly makes it yours for this round.
        chore.rotation_mode = "fixed"
    session.commit()
    session.refresh(chore)
    return chore


def month_bounds(year: int, month: int) -> tuple[dt.datetime, dt.datetime]:
    """Half-open [start, end) datetimes covering a calendar month.

    Timestamps are written by the database in UTC while "the month" is a
    human, local-timezone idea. For a house recap a few hours of drift at a
    month boundary is not worth carrying timezone-aware columns for, so the
    bounds are naive and compared as-is.
    """
    start = dt.datetime(year, month, 1)
    end = dt.datetime(year + (month == 12), month % 12 + 1, 1)
    return start, end


# The end-of-month honours, in the order they're handed out. Ranked purely on
# chores done -- shopping runs and spending are reported but don't score, because
# a title you can buy isn't worth winning.
AWARDS = (
    ("mvr", "MVR", "Most Valuable Roommate", "🏆", "Carried the house."),
    ("shlom", "The Shlom", "Neither hero nor villain", "🛋️", "Comfortably mid."),
    ("lvr", "LVR", "Least Valuable Roommate", "🦥", "There's always next month."),
)


def hand_out_awards(rows: list[dict]) -> list[dict]:
    """Rank the house by chores done and give out the three titles.

    Only current roommates are eligible -- handing a trophy to someone who moved
    out in week two isn't a joke anyone gets. Doing nothing is still eligible for
    LVR; that's rather the point of it.

    Ties share a title: two people on four chores are both MVR. If the entire
    house is level, nobody wins anything, because a table where everyone is
    simultaneously most and least valuable isn't a table.
    """
    eligible = [row for row in rows if row["member"].active]
    if len(eligible) < 2:
        return []

    tallies = {row["chores_done"] for row in eligible}
    if len(tallies) < 2:
        return []

    most, least = max(tallies), min(tallies)
    # A two-person house has a top and a bottom and nothing in between, so the
    # middle bucket comes out empty and The Shlom simply goes unawarded.
    buckets = {
        "mvr": [r for r in eligible if r["chores_done"] == most],
        "shlom": [r for r in eligible if least < r["chores_done"] < most],
        "lvr": [r for r in eligible if r["chores_done"] == least],
    }

    handed_out = []
    for key, title, subtitle, emoji, blurb in AWARDS:
        winners = sorted(
            buckets[key], key=lambda r: (-r["chores_done"], r["member"].name)
        )
        if not winners:
            continue
        handed_out.append(
            {
                "key": key,
                "title": title,
                "subtitle": subtitle,
                "emoji": emoji,
                "blurb": blurb,
                "winners": [
                    {"member": r["member"], "chores_done": r["chores_done"]}
                    for r in winners
                ],
            }
        )
    return handed_out


def recap(session: Session, year: int, month: int) -> dict:
    """Who did what over one month.

    This is the only place attribution surfaces, and only once the month is
    over. While it's still running the per-roommate numbers are withheld
    server-side rather than hidden in the page: a scoreboard you can peek at all
    month is just the constant tracking this app deliberately doesn't do.
    """
    start, end = month_bounds(year, month)
    members = list_members(session, include_inactive=True)
    by_id = {m.id: m for m in members}

    rows: dict[int, dict] = {
        m.id: {
            "member": member_out(m),
            "chores_done": 0,
            "chore_names": [],
            "items_bought": 0,
            "paid_cents": 0,
            "owed_cents": 0,
        }
        for m in members
    }

    completions = session.scalars(
        select(models.ChoreCompletion).where(
            models.ChoreCompletion.completed_at >= start,
            models.ChoreCompletion.completed_at < end,
        )
    )
    for completion in completions:
        row = rows.get(completion.member_id)
        if row is None:
            continue
        row["chores_done"] += 1
        if completion.chore is not None:
            row["chore_names"].append(completion.chore.name)

    bought = session.scalars(
        select(models.ShoppingItem).where(
            models.ShoppingItem.purchased_at >= start,
            models.ShoppingItem.purchased_at < end,
        )
    )
    for item in bought:
        row = rows.get(item.purchased_by_id)
        if row is not None:
            row["items_bought"] += 1

    expenses = list(
        session.scalars(
            select(models.Expense).where(
                models.Expense.spent_on >= start.date(),
                models.Expense.spent_on < end.date(),
            )
        )
    )
    for expense in expenses:
        row = rows.get(expense.paid_by_id)
        if row is not None:
            row["paid_cents"] += expense.amount_cents
        for share in expense.shares:
            share_row = rows.get(share.member_id)
            if share_row is not None:
                share_row["owed_cents"] += share.share_cents

    # Collapse repeated chores into "Dishes ×4" rather than four identical lines.
    for row in rows.values():
        tally: dict[str, int] = {}
        for name in row["chore_names"]:
            tally[name] = tally.get(name, 0) + 1
        row["chore_names"] = [
            {"name": name, "count": count}
            for name, count in sorted(tally.items(), key=lambda kv: -kv[1])
        ]
        row["net_cents"] = row["paid_cents"] - row["owed_cents"]

    ordered = sorted(
        rows.values(),
        key=lambda r: (-r["chores_done"], -r["items_bought"], r["member"].name),
    )
    # Only roommates who actually did something appear; a month of zeroes for
    # someone who was travelling reads as an accusation, not information.
    active = [r for r in ordered if r["chores_done"] or r["items_bought"] or r["paid_cents"]]

    on = today()
    revealed = (year, month) < (on.year, on.month)

    payload = {
        "month": f"{year:04d}-{month:02d}",
        "label": dt.date(year, month, 1).strftime("%B %Y"),
        "revealed": revealed,
        # Counts down to the reveal. Clamped at zero so the last day of the
        # month reads "today" rather than a negative number.
        "days_left": 0 if revealed else max((end.date() - on).days, 0),
        "is_current": (year, month) == (on.year, on.month),
        # House-wide totals are safe to show all month -- they say how the house
        # is doing without saying who to blame.
        "totals": {
            "chores": sum(r["chores_done"] for r in ordered),
            "items": sum(r["items_bought"] for r in ordered),
            "spent_cents": sum(e.amount_cents for e in expenses),
        },
    }

    if not revealed:
        return payload | {"rows": [], "everyone": [], "awards": []}

    return payload | {
        "rows": active,
        "everyone": ordered,
        "awards": hand_out_awards(ordered),
    }


# --------------------------------------------------------------------------
# Shopping
# --------------------------------------------------------------------------


def list_shopping(session: Session):
    return list(
        session.scalars(
            select(models.ShoppingItem).order_by(
                models.ShoppingItem.purchased_at.is_(None).desc(),
                models.ShoppingItem.category,
                models.ShoppingItem.created_at,
            )
        )
    )


def shopping_out(item: models.ShoppingItem) -> schemas.ShoppingItemOut:
    return schemas.ShoppingItemOut(
        id=item.id,
        name=item.name,
        note=item.note,
        category=item.category,
        is_staple=item.is_staple,
        purchased=item.purchased_at is not None,
        added_by=member_out(item.added_by),
        purchased_by=member_out(item.purchased_by),
    )


def add_shopping_item(
    session: Session, data: schemas.ShoppingItemIn, member_id: int | None
) -> models.ShoppingItem:
    item = models.ShoppingItem(
        name=data.name.strip(),
        note=data.note,
        category=data.category or "Groceries",
        is_staple=data.is_staple,
        added_by_id=member_id,
    )
    session.add(item)
    session.commit()
    return item


def update_shopping_item(
    session: Session, item: models.ShoppingItem, data: schemas.ShoppingItemIn
) -> models.ShoppingItem:
    """Correct an item after the fact -- rename it, note the brand, move it to
    another list. Whether it's been bought, and by whom, is left alone: that's
    the tick's job, not the edit form's."""
    item.name = data.name.strip()
    item.note = data.note
    item.category = data.category or "Groceries"
    item.is_staple = data.is_staple
    session.commit()
    session.refresh(item)
    return item


def toggle_shopping_item(
    session: Session, item: models.ShoppingItem, member_id: int | None
) -> models.ShoppingItem:
    if item.purchased_at is None:
        item.purchased_at = dt.datetime.now(dt.UTC)
        item.purchased_by_id = member_id
    else:
        item.purchased_at = None
        item.purchased_by_id = None
    session.commit()
    # The item was loaded with `purchased_by` already eager-loaded as None, and
    # assigning the foreign key doesn't update that. Refresh so the response
    # carries the roommate we just credited rather than a stale null.
    session.refresh(item)
    return item


def clear_purchased(session: Session) -> int:
    """Sweep bought items. Staples come back unticked for the next shop."""
    bought = list(
        session.scalars(
            select(models.ShoppingItem).where(
                models.ShoppingItem.purchased_at.is_not(None)
            )
        )
    )
    removed = 0
    for item in bought:
        if item.is_staple:
            item.purchased_at = None
            item.purchased_by_id = None
        else:
            session.delete(item)
            removed += 1
    session.commit()
    return removed


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------


def list_events(session: Session, start: dt.date, end: dt.date):
    return list(
        session.scalars(
            select(models.Event)
            .where(models.Event.starts_on >= start, models.Event.starts_on <= end)
            .order_by(models.Event.starts_on, models.Event.starts_at)
        )
    )


def event_out(event: models.Event) -> schemas.EventOut:
    return schemas.EventOut(
        id=event.id,
        title=event.title,
        starts_on=event.starts_on,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        all_day=event.starts_at is None,
        location=event.location,
        notes=event.notes,
        created_by=member_out(event.created_by),
    )


def create_event(
    session: Session, data: schemas.EventIn, member_id: int | None
) -> models.Event:
    event = models.Event(
        title=data.title.strip(),
        starts_on=data.starts_on,
        starts_at=data.starts_at,
        ends_at=data.ends_at,
        location=data.location,
        notes=data.notes,
        created_by_id=member_id,
    )
    session.add(event)
    session.commit()
    return event


def update_event(
    session: Session, event: models.Event, data: schemas.EventIn
) -> models.Event:
    """Move it, rename it, drop the time to make it all-day.

    `created_by` deliberately stays with whoever added it -- an edit isn't a
    change of ownership, and rewriting it would quietly erase who to ask about
    the landlord visit.
    """
    event.title = data.title.strip()
    event.starts_on = data.starts_on
    event.starts_at = data.starts_at
    event.ends_at = data.ends_at
    event.location = data.location
    event.notes = data.notes
    session.commit()
    session.refresh(event)
    return event


# --------------------------------------------------------------------------
# Expenses
# --------------------------------------------------------------------------


def list_expenses(session: Session, limit: int = 100):
    return list(
        session.scalars(
            select(models.Expense)
            .order_by(models.Expense.spent_on.desc(), models.Expense.id.desc())
            .limit(limit)
        )
    )


def expense_out(expense: models.Expense) -> schemas.ExpenseOut:
    return schemas.ExpenseOut(
        id=expense.id,
        description=expense.description,
        amount_cents=expense.amount_cents,
        paid_by=member_out(expense.paid_by),
        spent_on=expense.spent_on,
        settled=expense.settled,
        shares=[
            schemas.ShareOut(
                member=member_out(share.member),  # type: ignore[arg-type]
                share_cents=share.share_cents,
            )
            for share in expense.shares
            if share.member is not None
        ],
    )


def create_expense(session: Session, data: schemas.ExpenseIn) -> models.Expense:
    split_ids = data.split_between_ids or [m.id for m in list_members(session)]
    if not split_ids:
        raise ValueError("No roommates to split between")

    expense = models.Expense(
        description=data.description.strip(),
        amount_cents=data.amount_cents,
        paid_by_id=data.paid_by_id,
        spent_on=data.spent_on or today(),
    )
    session.add(expense)
    session.flush()

    for member_id, share_cents in money.split_evenly(
        data.amount_cents, split_ids
    ).items():
        session.add(
            models.ExpenseShare(
                expense_id=expense.id, member_id=member_id, share_cents=share_cents
            )
        )

    session.commit()
    session.refresh(expense)
    return expense


def ledger(session: Session) -> schemas.LedgerOut:
    """Who owes whom, considering only expenses that haven't been settled."""
    members = {m.id: m for m in list_members(session, include_inactive=True)}
    unsettled = list(
        session.scalars(
            select(models.Expense).where(models.Expense.settled.is_(False))
        )
    )

    rows = [
        (
            expense.paid_by_id,
            expense.amount_cents,
            {share.member_id: share.share_cents for share in expense.shares},
        )
        for expense in unsettled
    ]
    net = money.balances(rows)

    balances_out = [
        schemas.BalanceOut(member=member_out(members[mid]), net_cents=cents)  # type: ignore[arg-type]
        for mid, cents in sorted(net.items(), key=lambda kv: -kv[1])
        if mid in members
    ]
    transfers_out = [
        schemas.TransferOut(
            from_member=member_out(members[debtor]),  # type: ignore[arg-type]
            to_member=member_out(members[creditor]),  # type: ignore[arg-type]
            amount_cents=amount,
        )
        for debtor, creditor, amount in money.settle_up(net)
        if debtor in members and creditor in members
    ]
    return schemas.LedgerOut(balances=balances_out, transfers=transfers_out)


def settle_all(session: Session) -> int:
    """Mark every outstanding expense settled -- the house squared up."""
    unsettled = list(
        session.scalars(
            select(models.Expense).where(models.Expense.settled.is_(False))
        )
    )
    for expense in unsettled:
        expense.settled = True
    session.commit()
    return len(unsettled)


# --------------------------------------------------------------------------
# House board
# --------------------------------------------------------------------------


def list_notes(session: Session):
    return list(
        session.scalars(
            select(models.Note).order_by(
                models.Note.pinned.desc(), models.Note.created_at.desc()
            )
        )
    )


def note_out(note: models.Note) -> schemas.NoteOut:
    return schemas.NoteOut(
        id=note.id,
        body=note.body,
        pinned=note.pinned,
        author=member_out(note.author),
        created_at=note.created_at,
    )


def create_note(
    session: Session, data: schemas.NoteIn, member_id: int | None
) -> models.Note:
    note = models.Note(body=data.body.strip(), pinned=data.pinned, author_id=member_id)
    session.add(note)
    session.commit()
    return note
