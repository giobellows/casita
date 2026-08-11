"""Splitting bills and settling up."""

from __future__ import annotations

from app.money import balances, format_cents, settle_up, split_evenly


class TestSplit:
    def test_even_split(self):
        assert split_evenly(900, [1, 2, 3]) == {1: 300, 2: 300, 3: 300}

    def test_remainder_goes_to_the_front(self):
        assert split_evenly(1000, [1, 2, 3]) == {1: 334, 2: 333, 3: 333}

    def test_shares_always_sum_to_the_total(self):
        for total in (1, 7, 99, 100, 4999, 123457):
            for size in range(1, 8):
                shares = split_evenly(total, list(range(size)))
                assert sum(shares.values()) == total, (total, size)

    def test_single_person_owes_everything(self):
        assert split_evenly(1234, [5]) == {5: 1234}

    def test_no_one_to_split_between(self):
        assert split_evenly(1000, []) == {}

    def test_negative_total_is_a_refund(self):
        # A credit splits the same way, without truncation flipping the sign.
        shares = split_evenly(-1000, [1, 2, 3])
        assert sum(shares.values()) == -1000
        assert shares == {1: -334, 2: -333, 3: -333}


class TestBalances:
    def test_payer_is_owed_everyone_elses_share(self):
        net = balances([(1, 900, {1: 300, 2: 300, 3: 300})])
        assert net == {1: 600, 2: -300, 3: -300}

    def test_balances_net_to_zero(self):
        net = balances([
            (1, 900, {1: 300, 2: 300, 3: 300}),
            (2, 600, {1: 200, 2: 200, 3: 200}),
        ])
        assert sum(net.values()) == 0
        assert net[1] == 400

    def test_expense_with_no_payer_still_charges_shares(self):
        net = balances([(None, 300, {1: 150, 2: 150})])
        assert net == {1: -150, 2: -150}


class TestSettleUp:
    def test_simple_two_person_debt(self):
        assert settle_up({1: 500, 2: -500}) == [(2, 1, 500)]

    def test_transfers_clear_every_balance(self):
        net = {1: 600, 2: -300, 3: -300}
        transfers = settle_up(net)
        settled = dict(net)
        for debtor, creditor, amount in transfers:
            settled[debtor] += amount
            settled[creditor] -= amount
        assert all(v == 0 for v in settled.values())

    def test_at_most_n_minus_one_transfers(self):
        net = {1: 1000, 2: -400, 3: -300, 4: -300}
        assert len(settle_up(net)) <= len(net) - 1

    def test_everyone_square_needs_no_transfers(self):
        assert settle_up({1: 0, 2: 0}) == []


def test_formatting():
    assert format_cents(0) == "$0.00"
    assert format_cents(5) == "$0.05"
    assert format_cents(1234) == "$12.34"
    assert format_cents(-1205) == "-$12.05"
