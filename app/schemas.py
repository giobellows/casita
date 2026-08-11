"""Request and response shapes.

Response models are hand-built by `service.py` rather than serialised straight
off the ORM, because the frontend wants derived fields (a chore's status, whose
turn it is next, a member's balance) that don't exist as columns.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field, field_validator

from app.rotation import CADENCES, ROTATION_MODES

# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------


class LoginIn(BaseModel):
    passcode: str = ""


class IdentifyIn(BaseModel):
    member_id: int


# --------------------------------------------------------------------------
# Members
# --------------------------------------------------------------------------


class MemberIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    emoji: str = Field(default="🙂", max_length=16)
    color: str = Field(default="violet", max_length=24)


class MemberOut(BaseModel):
    id: int
    name: str
    emoji: str
    color: str
    active: bool


# --------------------------------------------------------------------------
# Chores
# --------------------------------------------------------------------------


class ChoreIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    emoji: str = Field(default="🧹", max_length=16)
    notes: str = Field(default="", max_length=2000)
    cadence: str = "weekly"
    interval_n: int = Field(default=1, ge=1, le=365)
    due_on: dt.date | None = None
    rotation_mode: str = "anyone"
    assignee_id: int | None = None
    rotation_member_ids: list[int] = Field(default_factory=list)

    @field_validator("cadence")
    @classmethod
    def _valid_cadence(cls, value: str) -> str:
        if value not in CADENCES:
            raise ValueError(f"cadence must be one of {CADENCES}")
        return value

    @field_validator("rotation_mode")
    @classmethod
    def _valid_rotation(cls, value: str) -> str:
        if value not in ROTATION_MODES:
            raise ValueError(f"rotation_mode must be one of {ROTATION_MODES}")
        return value


class ChoreOut(BaseModel):
    id: int
    name: str
    emoji: str
    notes: str
    cadence: str
    interval_n: int
    cadence_label: str
    due_on: dt.date
    status: str
    days_until: int
    rotation_mode: str
    assignee: MemberOut | None
    next_up: MemberOut | None
    rotation: list[MemberOut]
    archived: bool


class CompleteIn(BaseModel):
    """Who did it. Defaults to the browser's member when omitted."""

    member_id: int | None = None


class SnoozeIn(BaseModel):
    days: int = Field(default=1, ge=1, le=90)


class ReassignIn(BaseModel):
    member_id: int | None = None


# --------------------------------------------------------------------------
# Shopping
# --------------------------------------------------------------------------


class ShoppingItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=200)
    category: str = Field(default="Groceries", max_length=40)
    is_staple: bool = False


class ShoppingItemOut(BaseModel):
    id: int
    name: str
    note: str
    category: str
    is_staple: bool
    purchased: bool
    added_by: MemberOut | None
    purchased_by: MemberOut | None


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------


class EventIn(BaseModel):
    title: str = Field(min_length=1, max_length=140)
    starts_on: dt.date
    starts_at: dt.time | None = None
    ends_at: dt.time | None = None
    location: str = Field(default="", max_length=140)
    notes: str = Field(default="", max_length=2000)


class EventOut(BaseModel):
    id: int
    title: str
    starts_on: dt.date
    starts_at: dt.time | None
    ends_at: dt.time | None
    all_day: bool
    location: str
    notes: str
    created_by: MemberOut | None


# --------------------------------------------------------------------------
# Expenses
# --------------------------------------------------------------------------


class ExpenseIn(BaseModel):
    description: str = Field(min_length=1, max_length=140)
    amount_cents: int = Field(ge=1)
    paid_by_id: int
    spent_on: dt.date | None = None
    # Empty means "split across every active roommate".
    split_between_ids: list[int] = Field(default_factory=list)


class ShareOut(BaseModel):
    member: MemberOut
    share_cents: int


class ExpenseOut(BaseModel):
    id: int
    description: str
    amount_cents: int
    paid_by: MemberOut | None
    spent_on: dt.date
    settled: bool
    shares: list[ShareOut]


class BalanceOut(BaseModel):
    member: MemberOut
    net_cents: int


class TransferOut(BaseModel):
    from_member: MemberOut
    to_member: MemberOut
    amount_cents: int


class LedgerOut(BaseModel):
    balances: list[BalanceOut]
    transfers: list[TransferOut]


# --------------------------------------------------------------------------
# House board
# --------------------------------------------------------------------------


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    pinned: bool = False


class NoteOut(BaseModel):
    id: int
    body: str
    pinned: bool
    author: MemberOut | None
    created_at: dt.datetime
