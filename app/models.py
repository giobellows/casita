"""ORM models for the house.

Everything hangs off `Member` -- a roommate. There are deliberately no user
accounts with passwords: the house shares one passcode, and each browser
remembers which member it belongs to. That keeps attribution ("Sam took out the
trash") without making anyone sign up for anything.

The interesting model is `Chore`, which has two independent axes:

    cadence   daily / weekly / monthly  -> recurring, rolls forward on completion
              once                      -> a one-off task, done means done

    rotation  rotate -> cycles through an ordered list of roommates
              fixed  -> always the same person
              anyone -> unassigned, whoever gets to it

So "take out the trash, every week, rotating between the three of us" is
cadence=weekly + rotation=rotate, and completing it both advances `due_on` by a
week and hands the baton to the next person in the ring.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Member(Base):
    """A roommate."""

    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    emoji: Mapped[str] = mapped_column(String(16), default="🙂")
    # Tailwind-ish palette name; the frontend maps it to a real colour.
    color: Mapped[str] = mapped_column(String(24), default="violet")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ChoreRotationSlot(Base):
    """One seat in a chore's rotation ring, ordered by `position`."""

    __tablename__ = "chore_rotation"
    __table_args__ = (
        UniqueConstraint("chore_id", "member_id", name="uq_rotation_chore_member"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chore_id: Mapped[int] = mapped_column(
        ForeignKey("chores.id", ondelete="CASCADE"), index=True
    )
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)

    chore: Mapped[Chore] = relationship(back_populates="rotation")
    member: Mapped[Member] = relationship(lazy="joined")


class Chore(Base):
    __tablename__ = "chores"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    emoji: Mapped[str] = mapped_column(String(16), default="🧹")
    notes: Mapped[str] = mapped_column(Text, default="")

    cadence: Mapped[str] = mapped_column(String(16), default="weekly")
    # "every N cadences" -- 2 + weekly means biweekly. Always >= 1.
    interval_n: Mapped[int] = mapped_column(Integer, default=1)
    # The date this chore is next expected. Drives the whole "what's due" view.
    due_on: Mapped[dt.date] = mapped_column(Date, index=True)

    rotation_mode: Mapped[str] = mapped_column(String(16), default="anyone")
    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL"), nullable=True
    )

    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    assignee: Mapped[Member | None] = relationship(lazy="joined")
    rotation: Mapped[list[ChoreRotationSlot]] = relationship(
        back_populates="chore",
        cascade="all, delete-orphan",
        order_by="ChoreRotationSlot.position",
        lazy="selectin",
    )
    completions: Mapped[list[ChoreCompletion]] = relationship(
        back_populates="chore", cascade="all, delete-orphan"
    )


class ChoreCompletion(Base):
    """An audit row: who did what, when, and what it was due on.

    Keeping `due_on` lets us tell on-time from late after the fact, and makes the
    per-roommate contribution counts honest.
    """

    __tablename__ = "chore_completions"

    id: Mapped[int] = mapped_column(primary_key=True)
    chore_id: Mapped[int] = mapped_column(
        ForeignKey("chores.id", ondelete="CASCADE"), index=True
    )
    member_id: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL"), nullable=True, index=True
    )
    due_on: Mapped[dt.date] = mapped_column(Date)
    completed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    chore: Mapped[Chore] = relationship(back_populates="completions")
    member: Mapped[Member | None] = relationship(lazy="joined")


class ShoppingItem(Base):
    """Something the house needs. Bought items stick around until cleared."""

    __tablename__ = "shopping_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    note: Mapped[str] = mapped_column(String(200), default="")
    # Free-text so the house can invent its own ("Costco run", "Bodega").
    category: Mapped[str] = mapped_column(String(40), default="Groceries")
    # Staples reappear on the list after being cleared instead of vanishing.
    is_staple: Mapped[bool] = mapped_column(Boolean, default=False)

    added_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL"), nullable=True
    )
    purchased_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL"), nullable=True
    )
    purchased_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    added_by: Mapped[Member | None] = relationship(
        foreign_keys=[added_by_id], lazy="joined"
    )
    purchased_by: Mapped[Member | None] = relationship(
        foreign_keys=[purchased_by_id], lazy="joined"
    )


class Event(Base):
    """A house calendar entry. All-day events ignore the time component."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(140))
    starts_on: Mapped[dt.date] = mapped_column(Date, index=True)
    # Naive local times in HOUSE_TZ, or NULL for all-day events.
    starts_at: Mapped[dt.time | None] = mapped_column(nullable=True)
    ends_at: Mapped[dt.time | None] = mapped_column(nullable=True)
    location: Mapped[str] = mapped_column(String(140), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    created_by: Mapped[Member | None] = relationship(lazy="joined")


class Expense(Base):
    """A shared cost. Money is stored in integer cents -- never floats."""

    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    description: Mapped[str] = mapped_column(String(140))
    amount_cents: Mapped[int] = mapped_column(Integer)
    paid_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL"), nullable=True, index=True
    )
    spent_on: Mapped[dt.date] = mapped_column(Date, index=True)
    settled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    paid_by: Mapped[Member | None] = relationship(lazy="joined")
    shares: Mapped[list[ExpenseShare]] = relationship(
        back_populates="expense", cascade="all, delete-orphan", lazy="selectin"
    )


class ExpenseShare(Base):
    """How much of one expense a given roommate owes. Shares sum to the total."""

    __tablename__ = "expense_shares"
    __table_args__ = (
        UniqueConstraint("expense_id", "member_id", name="uq_share_expense_member"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    expense_id: Mapped[int] = mapped_column(
        ForeignKey("expenses.id", ondelete="CASCADE"), index=True
    )
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), index=True
    )
    share_cents: Mapped[int] = mapped_column(Integer)

    expense: Mapped[Expense] = relationship(back_populates="shares")
    member: Mapped[Member] = relationship(lazy="joined")


class Note(Base):
    """The house board -- announcements, house rules, wifi password, whatever."""

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    body: Mapped[str] = mapped_column(Text)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    author: Mapped[Member | None] = relationship(lazy="joined")
