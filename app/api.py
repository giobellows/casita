"""HTTP routes.

Two routers: `public` for the handful of endpoints that must work before you're
signed in, and `api` for everything else, which is gated by `require_auth`.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app import auth, config, models, schemas, service
from app.db import get_session

public = APIRouter(prefix="/api", tags=["public"])
api = APIRouter(prefix="/api", dependencies=[Depends(auth.require_auth)])


def _member_id(request: Request) -> int | None:
    return auth.current_member_id(request)


def _get_or_404(session: Session, model, object_id: int):
    instance = session.get(model, object_id)
    if instance is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No such {model.__name__}")
    return instance


# --------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------


@public.get("/me")
def me(request: Request, session: Session = Depends(get_session)):
    """Everything the frontend needs on boot, in one round trip."""
    signed_in = auth.is_authenticated(request)
    member = None
    if signed_in:
        member_id = auth.current_member_id(request)
        if member_id is not None:
            member = service.member_out(session.get(models.Member, member_id))

    return {
        "authenticated": signed_in,
        "auth_required": not auth.AUTH_DISABLED,
        "house_name": config.HOUSE_NAME,
        "member": member,
        "members": [service.member_out(m) for m in service.list_members(session)]
        if signed_in
        else [],
        "today": service.today().isoformat(),
    }


@public.post("/login")
def login(data: schemas.LoginIn, response: Response):
    if not auth.check_passcode(data.passcode):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That passcode isn't right")
    auth.set_cookie(response, config.SESSION_COOKIE, auth.issue_session())
    return {"ok": True}


@public.post("/logout")
def logout(response: Response):
    response.delete_cookie(config.SESSION_COOKIE)
    response.delete_cookie(config.MEMBER_COOKIE)
    return {"ok": True}


@api.post("/identify")
def identify(
    data: schemas.IdentifyIn,
    response: Response,
    session: Session = Depends(get_session),
):
    """Tell this browser which roommate is using it."""
    member = _get_or_404(session, models.Member, data.member_id)
    auth.set_cookie(response, config.MEMBER_COOKIE, auth.issue_member(member.id))
    return {"ok": True, "member": service.member_out(member)}


# --------------------------------------------------------------------------
# Members
# --------------------------------------------------------------------------


@api.get("/members")
def get_members(
    include_inactive: bool = False, session: Session = Depends(get_session)
):
    return [
        service.member_out(m)
        for m in service.list_members(session, include_inactive=include_inactive)
    ]


@api.post("/members", status_code=status.HTTP_201_CREATED)
def post_member(data: schemas.MemberIn, session: Session = Depends(get_session)):
    return service.member_out(service.create_member(session, data))


@api.put("/members/{member_id}")
def put_member(
    member_id: int, data: schemas.MemberIn, session: Session = Depends(get_session)
):
    member = _get_or_404(session, models.Member, member_id)
    return service.member_out(service.update_member(session, member, data))


@api.delete("/members/{member_id}")
def delete_member(member_id: int, session: Session = Depends(get_session)):
    member = _get_or_404(session, models.Member, member_id)
    service.deactivate_member(session, member)
    return {"ok": True}


# --------------------------------------------------------------------------
# Chores
# --------------------------------------------------------------------------


@api.get("/chores")
def get_chores(
    include_archived: bool = False, session: Session = Depends(get_session)
):
    on = service.today()
    return [
        service.chore_out(c, on)
        for c in service.list_chores(session, include_archived=include_archived)
    ]


@api.post("/chores", status_code=status.HTTP_201_CREATED)
def post_chore(data: schemas.ChoreIn, session: Session = Depends(get_session)):
    chore = service.create_chore(session, data)
    return service.chore_out(chore, service.today())


@api.put("/chores/{chore_id}")
def put_chore(
    chore_id: int, data: schemas.ChoreIn, session: Session = Depends(get_session)
):
    chore = _get_or_404(session, models.Chore, chore_id)
    return service.chore_out(service.update_chore(session, chore, data), service.today())


@api.delete("/chores/{chore_id}")
def delete_chore(chore_id: int, session: Session = Depends(get_session)):
    chore = _get_or_404(session, models.Chore, chore_id)
    session.delete(chore)
    session.commit()
    return {"ok": True}


@api.post("/chores/{chore_id}/complete")
def complete_chore(
    chore_id: int,
    data: schemas.CompleteIn,
    request: Request,
    session: Session = Depends(get_session),
):
    chore = _get_or_404(session, models.Chore, chore_id)
    # Whoever is named wins; otherwise credit whoever's browser this is, and
    # fall back to the person it was assigned to.
    doer = data.member_id or _member_id(request) or chore.assignee_id
    return service.chore_out(
        service.complete_chore(session, chore, doer), service.today()
    )


@api.post("/chores/{chore_id}/snooze")
def snooze_chore(
    chore_id: int, data: schemas.SnoozeIn, session: Session = Depends(get_session)
):
    chore = _get_or_404(session, models.Chore, chore_id)
    return service.chore_out(
        service.snooze_chore(session, chore, data.days), service.today()
    )


@api.post("/chores/{chore_id}/reassign")
def reassign_chore(
    chore_id: int, data: schemas.ReassignIn, session: Session = Depends(get_session)
):
    chore = _get_or_404(session, models.Chore, chore_id)
    return service.chore_out(
        service.reassign_chore(session, chore, data.member_id), service.today()
    )


# --------------------------------------------------------------------------
# Shopping
# --------------------------------------------------------------------------


@api.get("/shopping")
def get_shopping(session: Session = Depends(get_session)):
    return [service.shopping_out(i) for i in service.list_shopping(session)]


@api.post("/shopping", status_code=status.HTTP_201_CREATED)
def post_shopping(
    data: schemas.ShoppingItemIn,
    request: Request,
    session: Session = Depends(get_session),
):
    item = service.add_shopping_item(session, data, _member_id(request))
    return service.shopping_out(item)


@api.post("/shopping/{item_id}/toggle")
def toggle_shopping(
    item_id: int, request: Request, session: Session = Depends(get_session)
):
    item = _get_or_404(session, models.ShoppingItem, item_id)
    return service.shopping_out(
        service.toggle_shopping_item(session, item, _member_id(request))
    )


@api.delete("/shopping/{item_id}")
def delete_shopping(item_id: int, session: Session = Depends(get_session)):
    item = _get_or_404(session, models.ShoppingItem, item_id)
    session.delete(item)
    session.commit()
    return {"ok": True}


@api.post("/shopping/clear")
def clear_shopping(session: Session = Depends(get_session)):
    return {"removed": service.clear_purchased(session)}


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------


@api.get("/events")
def get_events(
    start: dt.date | None = None,
    end: dt.date | None = None,
    session: Session = Depends(get_session),
):
    on = service.today()
    start = start or on - dt.timedelta(days=7)
    end = end or on + dt.timedelta(days=120)
    return [service.event_out(e) for e in service.list_events(session, start, end)]


@api.post("/events", status_code=status.HTTP_201_CREATED)
def post_event(
    data: schemas.EventIn, request: Request, session: Session = Depends(get_session)
):
    event = service.create_event(session, data, _member_id(request))
    return service.event_out(event)


@api.delete("/events/{event_id}")
def delete_event(event_id: int, session: Session = Depends(get_session)):
    event = _get_or_404(session, models.Event, event_id)
    session.delete(event)
    session.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# Expenses
# --------------------------------------------------------------------------


@api.get("/expenses")
def get_expenses(session: Session = Depends(get_session)):
    return [service.expense_out(e) for e in service.list_expenses(session)]


@api.post("/expenses", status_code=status.HTTP_201_CREATED)
def post_expense(data: schemas.ExpenseIn, session: Session = Depends(get_session)):
    try:
        expense = service.create_expense(session, data)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return service.expense_out(expense)


@api.delete("/expenses/{expense_id}")
def delete_expense(expense_id: int, session: Session = Depends(get_session)):
    expense = _get_or_404(session, models.Expense, expense_id)
    session.delete(expense)
    session.commit()
    return {"ok": True}


@api.get("/ledger")
def get_ledger(session: Session = Depends(get_session)):
    return service.ledger(session)


@api.post("/expenses/settle")
def settle(session: Session = Depends(get_session)):
    return {"settled": service.settle_all(session)}


# --------------------------------------------------------------------------
# House board
# --------------------------------------------------------------------------


@api.get("/notes")
def get_notes(session: Session = Depends(get_session)):
    return [service.note_out(n) for n in service.list_notes(session)]


@api.post("/notes", status_code=status.HTTP_201_CREATED)
def post_note(
    data: schemas.NoteIn, request: Request, session: Session = Depends(get_session)
):
    return service.note_out(service.create_note(session, data, _member_id(request)))


@api.post("/notes/{note_id}/pin")
def pin_note(note_id: int, session: Session = Depends(get_session)):
    note = _get_or_404(session, models.Note, note_id)
    note.pinned = not note.pinned
    session.commit()
    return service.note_out(note)


@api.delete("/notes/{note_id}")
def delete_note(note_id: int, session: Session = Depends(get_session)):
    note = _get_or_404(session, models.Note, note_id)
    session.delete(note)
    session.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------


@api.get("/summary")
def summary(request: Request, session: Session = Depends(get_session)):
    """The home screen, assembled server-side so the phone does one fetch."""
    on = service.today()
    chores = [service.chore_out(c, on) for c in service.list_chores(session)]
    me_id = _member_id(request)

    events = service.list_events(session, on, on + dt.timedelta(days=14))
    shopping = [i for i in service.list_shopping(session) if i.purchased_at is None]

    net_cents = 0
    if me_id is not None:
        for balance in service.ledger(session).balances:
            if balance.member and balance.member.id == me_id:
                net_cents = balance.net_cents

    return {
        "today": on.isoformat(),
        "overdue": [c for c in chores if c.status == "overdue"],
        "due_today": [c for c in chores if c.status == "today"],
        "this_week": [c for c in chores if c.status == "soon"],
        "upcoming_events": [service.event_out(e) for e in events[:5]],
        "shopping_count": len(shopping),
        "my_balance_cents": net_cents,
    }


@api.get("/recap")
def get_recap(month: str | None = None, session: Session = Depends(get_session)):
    """Who did what, for one month. `month` is YYYY-MM; defaults to this one."""
    on = service.today()
    year, month_number = on.year, on.month
    if month:
        try:
            year, month_number = (int(part) for part in month.split("-", 1))
            dt.date(year, month_number, 1)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "month must look like 2026-08"
            ) from exc
    return service.recap(session, year, month_number)
