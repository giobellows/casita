"""Splitting shared costs.

Money is integer cents everywhere. The only genuinely tricky part is that most
bills don't divide evenly, so somebody has to eat the extra penny -- this module
makes that deterministic and, over time, fair.
"""

from __future__ import annotations


def split_evenly(amount_cents: int, member_ids: list[int]) -> dict[int, int]:
    """Split a cost across roommates so the shares sum to exactly the total.

    $10 across 3 people is 334/333/333, not 333.33 each. The leftover pennies go
    to the earliest members in the list, which is arbitrary but consistent, and
    the caller passes members in a stable order so it evens out across bills.
    """
    if not member_ids:
        return {}

    count = len(member_ids)
    # Integer division truncates toward zero, which is wrong for refunds
    # (negative totals), so do the arithmetic on the magnitude and re-apply sign.
    sign = -1 if amount_cents < 0 else 1
    magnitude = abs(amount_cents)

    base, remainder = divmod(magnitude, count)
    shares: dict[int, int] = {}
    for index, member_id in enumerate(member_ids):
        share = base + (1 if index < remainder else 0)
        shares[member_id] = sign * share
    return shares


def balances(
    expenses: list[tuple[int | None, int, dict[int, int]]],
) -> dict[int, int]:
    """Net position per roommate, in cents.

    Each expense is (paid_by_id, amount_cents, {member_id: share_cents}).
    Positive means the house owes them; negative means they owe the house.
    """
    net: dict[int, int] = {}
    for paid_by_id, amount_cents, shares in expenses:
        if paid_by_id is not None:
            net[paid_by_id] = net.get(paid_by_id, 0) + amount_cents
        for member_id, share_cents in shares.items():
            net[member_id] = net.get(member_id, 0) - share_cents
    return net


def settle_up(net: dict[int, int]) -> list[tuple[int, int, int]]:
    """Turn net balances into a short list of who pays whom.

    Greedy largest-debtor-to-largest-creditor. It won't always find the
    theoretical minimum number of transfers, but it's close, and it beats
    everyone paying everyone: N roommates need at most N-1 payments.
    """
    creditors = sorted(
        ((m, c) for m, c in net.items() if c > 0), key=lambda kv: -kv[1]
    )
    debtors = sorted(((m, -c) for m, c in net.items() if c < 0), key=lambda kv: -kv[1])

    transfers: list[tuple[int, int, int]] = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        debtor, owed = debtors[i]
        creditor, due = creditors[j]
        amount = min(owed, due)
        if amount > 0:
            transfers.append((debtor, creditor, amount))
        owed -= amount
        due -= amount
        debtors[i] = (debtor, owed)
        creditors[j] = (creditor, due)
        if owed == 0:
            i += 1
        if due == 0:
            j += 1
    return transfers


def format_cents(cents: int) -> str:
    """Render cents as a signed dollar string, e.g. -$12.05."""
    sign = "-" if cents < 0 else ""
    magnitude = abs(cents)
    return f"{sign}${magnitude // 100}.{magnitude % 100:02d}"
