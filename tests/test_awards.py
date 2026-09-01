"""The end-of-month honours: MVR, The Shlom, LVR.

Ranking is pure -- it takes recap rows and returns titles -- so it's tested
directly rather than through a month of simulated chores.
"""

from __future__ import annotations

from app.schemas import MemberOut
from app.service import hand_out_awards


def row(name: str, chores_done: int, active: bool = True) -> dict:
    return {
        "member": MemberOut(
            id=ord(name[0]), name=name, emoji="🙂", color="violet", active=active
        ),
        "chores_done": chores_done,
    }


def titles(awards: list[dict]) -> dict[str, list[str]]:
    """{"mvr": ["Sam"], ...} -- the shape the assertions actually care about."""
    return {
        award["key"]: [w["member"].name for w in award["winners"]]
        for award in awards
    }


class TestTheUsualCase:
    house = [row("Gio", 9), row("Sam", 5), row("Ali", 1)]

    def test_hands_out_all_three(self):
        assert titles(hand_out_awards(self.house)) == {
            "mvr": ["Gio"],
            "shlom": ["Sam"],
            "lvr": ["Ali"],
        }

    def test_order_is_trophy_first(self):
        assert [a["key"] for a in hand_out_awards(self.house)] == [
            "mvr",
            "shlom",
            "lvr",
        ]

    def test_carries_the_count_that_won_it(self):
        mvr = hand_out_awards(self.house)[0]
        assert mvr["winners"][0]["chores_done"] == 9
        assert mvr["title"] == "MVR"
        assert mvr["subtitle"] == "Most Valuable Roommate"

    def test_input_order_does_not_decide_the_winner(self):
        shuffled = [row("Ali", 1), row("Gio", 9), row("Sam", 5)]
        assert titles(hand_out_awards(shuffled)) == titles(hand_out_awards(self.house))


class TestTies:
    def test_a_tie_at_the_top_shares_the_trophy(self):
        awards = hand_out_awards([row("Gio", 7), row("Sam", 7), row("Ali", 2)])
        assert titles(awards) == {"mvr": ["Gio", "Sam"], "lvr": ["Ali"]}

    def test_a_tie_at_the_bottom_shares_the_shame(self):
        awards = hand_out_awards([row("Gio", 7), row("Sam", 2), row("Ali", 2)])
        assert titles(awards) == {"mvr": ["Gio"], "lvr": ["Ali", "Sam"]}

    def test_a_dead_heat_awards_nothing(self):
        """Everyone level means nobody is most or least anything."""
        assert hand_out_awards([row("Gio", 4), row("Sam", 4), row("Ali", 4)]) == []

    def test_a_house_that_did_nothing_at_all_awards_nothing(self):
        assert hand_out_awards([row("Gio", 0), row("Sam", 0)]) == []

    def test_joint_winners_are_listed_alphabetically(self):
        awards = hand_out_awards([row("Sam", 7), row("Ali", 7), row("Gio", 1)])
        assert titles(awards)["mvr"] == ["Ali", "Sam"]


class TestHouseSize:
    def test_two_roommates_get_no_shlom(self):
        """There's no middle of a two-person house to be in."""
        awards = hand_out_awards([row("Gio", 6), row("Sam", 2)])
        assert titles(awards) == {"mvr": ["Gio"], "lvr": ["Sam"]}

    def test_living_alone_wins_nothing(self):
        assert hand_out_awards([row("Gio", 12)]) == []

    def test_an_empty_house_wins_nothing(self):
        assert hand_out_awards([]) == []

    def test_everyone_between_the_ends_is_a_shlom(self):
        awards = hand_out_awards(
            [row("Gio", 9), row("Sam", 6), row("Ali", 4), row("Bea", 1)]
        )
        assert titles(awards)["shlom"] == ["Sam", "Ali"]

    def test_shloms_are_ranked_by_effort(self):
        awards = hand_out_awards(
            [row("Gio", 9), row("Ali", 4), row("Sam", 6), row("Bea", 1)]
        )
        # Within a shared title the busier roommate is listed first.
        assert titles(awards)["shlom"] == ["Sam", "Ali"]


class TestEligibility:
    def test_doing_nothing_still_earns_the_lvr(self):
        """The whole joke depends on a zero being a score, not an absence."""
        awards = hand_out_awards([row("Gio", 5), row("Sam", 3), row("Ali", 0)])
        assert titles(awards)["lvr"] == ["Ali"]

    def test_a_roommate_who_moved_out_is_not_ranked(self):
        awards = hand_out_awards(
            [row("Gio", 5), row("Sam", 3), row("Ghost", 0, active=False)]
        )
        assert titles(awards) == {"mvr": ["Gio"], "lvr": ["Sam"]}

    def test_a_departed_roommate_cannot_take_the_trophy(self):
        awards = hand_out_awards(
            [row("Ghost", 40, active=False), row("Gio", 5), row("Sam", 3)]
        )
        assert titles(awards)["mvr"] == ["Gio"]

    def test_one_survivor_wins_nothing(self):
        assert hand_out_awards([row("Gio", 5), row("Ghost", 3, active=False)]) == []
