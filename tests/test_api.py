"""End-to-end API behaviour."""

from __future__ import annotations

import datetime as dt


def sealed_recap(client):
    """This month's recap, read while the month is still running."""
    stamped = dt.datetime.now(dt.UTC).date()
    return client.get(f"/api/recap?month={stamped:%Y-%m}").json()


def revealed_recap(client, monkeypatch):
    """This month's recap, read as though the month had ended.

    Completions are stamped by the database in UTC as they happen, and a recap
    only unseals once the month it covers is in the past. So the way to see a
    revealed one in a test is to wind the house's clock forward rather than to
    fake the completion timestamps.
    """
    stamped = dt.datetime.now(dt.UTC).date()
    monkeypatch.setattr("app.service.today", lambda: dt.date(stamped.year + 1, 1, 1))
    return client.get(f"/api/recap?month={stamped:%Y-%m}").json()


class TestMembers:
    def test_create_and_list(self, client):
        client.post("/api/members", json={"name": "Gio", "emoji": "🐙"})
        members = client.get("/api/members").json()
        assert [m["name"] for m in members] == ["Gio"]

    def test_removed_roommate_is_hidden_but_kept(self, client, house):
        client.delete(f"/api/members/{house[2]['id']}")
        assert len(client.get("/api/members").json()) == 2
        assert len(client.get("/api/members?include_inactive=true").json()) == 3


class TestChores:
    def _rotating(self, client, house, **overrides):
        payload = {
            "name": "Trash",
            "cadence": "weekly",
            "rotation_mode": "rotate",
            "rotation_member_ids": [m["id"] for m in house],
        }
        payload.update(overrides)
        return client.post("/api/chores", json=payload).json()

    def test_rotation_starts_at_the_front(self, client, house):
        chore = self._rotating(client, house)
        assert chore["assignee"]["name"] == "Gio"
        assert chore["next_up"]["name"] == "Sam"

    def test_completing_passes_the_baton_and_rolls_the_date(self, client, house):
        chore = self._rotating(client, house, due_on="2026-08-10")
        done = client.post(f"/api/chores/{chore['id']}/complete", json={}).json()
        assert done["assignee"]["name"] == "Sam"
        assert done["due_on"] > "2026-08-10"

    def test_rotation_wraps_all_the_way_round(self, client, house):
        chore = self._rotating(client, house)
        names = []
        for _ in range(4):
            done = client.post(f"/api/chores/{chore['id']}/complete", json={}).json()
            names.append(done["assignee"]["name"])
        assert names == ["Sam", "Ali", "Gio", "Sam"]

    def test_one_off_chore_is_archived_when_done(self, client, house):
        chore = client.post(
            "/api/chores", json={"name": "Fix the door", "cadence": "once"}
        ).json()
        done = client.post(f"/api/chores/{chore['id']}/complete", json={}).json()
        assert done["archived"] is True
        assert client.get("/api/chores").json() == []

    def test_completion_is_credited_quietly_to_the_browsers_member(
        self, client, house, monkeypatch
    ):
        chore = self._rotating(client, house)
        client.post(f"/api/chores/{chore['id']}/complete", json={})
        # Attribution never appears in the live views, only in the recap, and
        # only once the month it belongs to is over.
        rows = revealed_recap(client, monkeypatch)["rows"]
        assert [(r["member"]["name"], r["chores_done"]) for r in rows] == [("Gio", 1)]

    def test_explicit_doer_overrides_the_cookie(self, client, house, monkeypatch):
        chore = self._rotating(client, house)
        client.post(
            f"/api/chores/{chore['id']}/complete", json={"member_id": house[2]["id"]}
        )
        rows = revealed_recap(client, monkeypatch)["rows"]
        assert [(r["member"]["name"], r["chores_done"]) for r in rows] == [("Ali", 1)]

    def test_chores_are_unassigned_unless_asked_for(self, client, house):
        """Assignment is available but never forced -- the default is nobody."""
        chore = client.post("/api/chores", json={"name": "Water the plants"}).json()
        assert chore["assignee"] is None
        assert chore["rotation_mode"] == "anyone"
        assert chore["rotation"] == []

    def test_an_unassigned_chore_can_still_be_completed(self, client, house, monkeypatch):
        chore = client.post("/api/chores", json={"name": "Sweep", "cadence": "weekly"}).json()
        done = client.post(f"/api/chores/{chore['id']}/complete", json={}).json()
        assert done["assignee"] is None
        rows = revealed_recap(client, monkeypatch)["rows"]
        assert rows[0]["chores_done"] == 1

    def test_snooze_pushes_the_due_date(self, client, house):
        chore = self._rotating(client, house, due_on="2026-08-10")
        out = client.post(f"/api/chores/{chore['id']}/snooze", json={"days": 3}).json()
        assert out["due_on"] == "2026-08-13"

    def test_claiming_an_unassigned_chore_makes_it_yours(self, client, house):
        chore = client.post(
            "/api/chores", json={"name": "Water plants", "rotation_mode": "anyone"}
        ).json()
        assert chore["assignee"] is None
        out = client.post(
            f"/api/chores/{chore['id']}/reassign", json={"member_id": house[1]["id"]}
        ).json()
        assert out["assignee"]["name"] == "Sam"
        assert out["rotation_mode"] == "fixed"

    def test_dropping_the_assignee_from_the_ring_restarts_it(self, client, house):
        chore = self._rotating(client, house)
        assert chore["assignee"]["name"] == "Gio"
        updated = client.put(
            f"/api/chores/{chore['id']}",
            json={
                "name": "Trash",
                "cadence": "weekly",
                "rotation_mode": "rotate",
                "rotation_member_ids": [house[1]["id"], house[2]["id"]],
            },
        ).json()
        assert updated["assignee"]["name"] == "Sam"

    def test_overdue_shows_up_in_the_summary(self, client, house):
        past = (dt.date.today() - dt.timedelta(days=2)).isoformat()
        self._rotating(client, house, due_on=past)
        summary = client.get("/api/summary").json()
        assert len(summary["overdue"]) == 1

    def test_rejects_a_nonsense_cadence(self, client):
        res = client.post("/api/chores", json={"name": "x", "cadence": "hourly"})
        assert res.status_code == 422


class TestDailyRefresh:
    """Opening the app rolls missed chores into today rather than piling them up."""

    def _stale(self, client, days_ago, **overrides):
        payload = {
            "name": "Dishes",
            "cadence": "daily",
            "due_on": (dt.date.today() - dt.timedelta(days=days_ago)).isoformat(),
        }
        payload.update(overrides)
        return client.post("/api/chores", json=payload).json()

    def test_listing_refreshes_a_stale_daily_chore(self, client, house):
        self._stale(client, 19)
        chore = client.get("/api/chores").json()[0]
        assert chore["due_on"] == dt.date.today().isoformat()
        assert chore["status"] == "today"
        assert chore["days_until"] == 0

    def test_the_home_screen_refreshes_it_too(self, client, house):
        self._stale(client, 19)
        summary = client.get("/api/summary").json()
        assert len(summary["due_today"]) == 1
        assert summary["overdue"] == []

    def test_the_refresh_is_saved_not_just_displayed(self, client, house):
        chore = self._stale(client, 19)
        client.get("/api/chores")
        # Read back through an endpoint that does no catching up of its own.
        again = client.post(f"/api/chores/{chore['id']}/snooze", json={"days": 1}).json()
        assert again["due_on"] == (dt.date.today() + dt.timedelta(days=1)).isoformat()

    def test_a_weekly_chore_stays_overdue_within_its_week(self, client, house):
        self._stale(client, 2, cadence="weekly")
        chore = client.get("/api/chores").json()[0]
        assert chore["status"] == "overdue"
        assert chore["days_until"] == -2

    def test_a_long_neglected_weekly_chore_is_at_most_a_week_late(self, client, house):
        self._stale(client, 60, cadence="weekly")
        chore = client.get("/api/chores").json()[0]
        assert -7 < chore["days_until"] <= 0

    def test_a_one_off_keeps_its_full_lateness(self, client, house):
        """Nothing to roll into -- "fix the door" really is three weeks late."""
        self._stale(client, 21, cadence="once")
        chore = client.get("/api/chores").json()[0]
        assert chore["days_until"] == -21

    def test_refreshing_does_not_pass_the_baton(self, client, house):
        """Nobody did it, so it's still your turn."""
        chore = self._stale(
            client,
            19,
            rotation_mode="rotate",
            rotation_member_ids=[m["id"] for m in house],
        )
        assert chore["assignee"]["name"] == "Gio"
        refreshed = client.get("/api/chores").json()[0]
        assert refreshed["assignee"]["name"] == "Gio"

    def test_refreshing_is_not_a_completion(self, client, house, monkeypatch):
        """A skipped chore must not credit anyone in the recap."""
        self._stale(client, 19)
        client.get("/api/chores")
        recap = revealed_recap(client, monkeypatch)
        assert recap["totals"]["chores"] == 0
        assert recap["rows"] == []

    def test_a_snoozed_chore_is_left_where_it_was_put(self, client, house):
        chore = self._stale(client, 19)
        client.post(f"/api/chores/{chore['id']}/snooze", json={"days": 30})
        expected = (dt.date.today() + dt.timedelta(days=11)).isoformat()
        assert client.get("/api/chores").json()[0]["due_on"] == expected


class TestUndo:
    """Marking something done by mistake has to be reversible."""

    def _rotating(self, client, house, **overrides):
        payload = {
            "name": "Trash",
            "cadence": "weekly",
            "rotation_mode": "rotate",
            "rotation_member_ids": [m["id"] for m in house],
        }
        payload.update(overrides)
        return client.post("/api/chores", json=payload).json()

    def test_restores_due_date_and_whose_turn(self, client, house):
        chore = self._rotating(client, house, due_on="2026-08-10")
        client.post(f"/api/chores/{chore['id']}/complete", json={})

        undone = client.post(f"/api/chores/{chore['id']}/undo", json={}).json()
        assert undone["due_on"] == "2026-08-10"
        assert undone["assignee"]["name"] == "Gio"

    def test_round_trips_any_number_of_times(self, client, house):
        chore = self._rotating(client, house, due_on="2026-08-10")
        for _ in range(4):
            client.post(f"/api/chores/{chore['id']}/complete", json={})
            undone = client.post(f"/api/chores/{chore['id']}/undo", json={}).json()
            assert undone["due_on"] == "2026-08-10"
            assert undone["assignee"]["name"] == "Gio"

    def test_restores_the_real_date_for_a_neglected_chore(self, client, house):
        """The due date is read back, not recomputed.

        Completing a long-overdue chore catches it up past today, so stepping
        back one period would land on a date it was never due.
        """
        chore = self._rotating(client, house, due_on="2026-06-01")
        client.post(f"/api/chores/{chore['id']}/complete", json={})
        undone = client.post(f"/api/chores/{chore['id']}/undo", json={}).json()
        assert undone["due_on"] == "2026-06-01"

    def test_brings_back_a_one_off(self, client, house):
        chore = client.post(
            "/api/chores", json={"name": "Fix the door", "cadence": "once"}
        ).json()
        client.post(f"/api/chores/{chore['id']}/complete", json={})
        assert client.get("/api/chores").json() == []

        undone = client.post(f"/api/chores/{chore['id']}/undo", json={}).json()
        assert undone["archived"] is False
        assert len(client.get("/api/chores").json()) == 1

    def test_removes_it_from_the_monthly_recap(self, client, house):
        chore = client.post("/api/chores", json={"name": "Dishes"}).json()
        client.post(f"/api/chores/{chore['id']}/complete", json={})
        assert client.get("/api/recap").json()["totals"]["chores"] == 1

        client.post(f"/api/chores/{chore['id']}/undo", json={})
        recap = client.get("/api/recap").json()
        assert recap["totals"]["chores"] == 0
        assert recap["rows"] == []

    def test_undoes_only_the_most_recent(self, client, house, monkeypatch):
        chore = client.post("/api/chores", json={"name": "Dishes", "cadence": "daily"}).json()
        client.post(f"/api/chores/{chore['id']}/complete", json={"member_id": house[0]["id"]})
        client.post(f"/api/chores/{chore['id']}/complete", json={"member_id": house[1]["id"]})

        client.post(f"/api/chores/{chore['id']}/undo", json={})
        rows = revealed_recap(client, monkeypatch)["rows"]
        # Sam's tick is reversed; Gio's earlier one survives.
        assert [(r["member"]["name"], r["chores_done"]) for r in rows] == [("Gio", 1)]

    def test_unassigned_chore_undoes_fine(self, client, house):
        chore = client.post(
            "/api/chores", json={"name": "Sweep", "due_on": "2026-08-10"}
        ).json()
        client.post(f"/api/chores/{chore['id']}/complete", json={})
        undone = client.post(f"/api/chores/{chore['id']}/undo", json={}).json()
        assert undone["due_on"] == "2026-08-10"
        assert undone["assignee"] is None

    def test_nothing_to_undo_is_a_clear_error(self, client, house):
        chore = client.post("/api/chores", json={"name": "Dishes"}).json()
        res = client.post(f"/api/chores/{chore['id']}/undo", json={})
        assert res.status_code == 400
        assert "hasn't been completed" in res.json()["detail"]

    def test_undoing_a_missing_chore_is_a_404(self, client, house):
        assert client.post("/api/chores/9999/undo", json={}).status_code == 404


class TestShopping:
    def test_add_toggle_and_clear(self, client, house):
        item = client.post("/api/shopping", json={"name": "Oat milk"}).json()
        assert item["added_by"]["name"] == "Gio"
        assert item["purchased"] is False

        toggled = client.post(f"/api/shopping/{item['id']}/toggle", json={}).json()
        assert toggled["purchased"] is True
        assert toggled["purchased_by"]["name"] == "Gio"

        client.post("/api/shopping/clear", json={})
        assert client.get("/api/shopping").json() == []

    def test_an_item_can_be_corrected_after_the_fact(self, client, house):
        item = client.post("/api/shopping", json={"name": "Milk"}).json()
        edited = client.put(
            f"/api/shopping/{item['id']}",
            json={
                "name": "Oat milk",
                "note": "the barista one",
                "category": "Fridge",
                "is_staple": True,
            },
        ).json()
        assert edited["name"] == "Oat milk"
        assert edited["note"] == "the barista one"
        assert edited["category"] == "Fridge"
        assert edited["is_staple"] is True

    def test_editing_leaves_the_tick_alone(self, client, house):
        """Renaming something in the cart shouldn't put it back on the list."""
        item = client.post("/api/shopping", json={"name": "Milk"}).json()
        client.post(f"/api/shopping/{item['id']}/toggle", json={})

        edited = client.put(
            f"/api/shopping/{item['id']}", json={"name": "Oat milk"}
        ).json()
        assert edited["purchased"] is True
        assert edited["purchased_by"]["name"] == "Gio"

    def test_editing_keeps_who_added_it(self, client, house):
        item = client.post("/api/shopping", json={"name": "Milk"}).json()
        edited = client.put(
            f"/api/shopping/{item['id']}", json={"name": "Oat milk"}
        ).json()
        assert edited["added_by"]["name"] == "Gio"

    def test_editing_a_missing_item_is_a_404(self, client, house):
        assert client.put("/api/shopping/9999", json={"name": "x"}).status_code == 404

    def test_an_edit_still_needs_a_name(self, client, house):
        item = client.post("/api/shopping", json={"name": "Milk"}).json()
        assert client.put(f"/api/shopping/{item['id']}", json={"name": ""}).status_code == 422

    def test_staples_come_back_unticked(self, client, house):
        item = client.post(
            "/api/shopping", json={"name": "Toilet paper", "is_staple": True}
        ).json()
        client.post(f"/api/shopping/{item['id']}/toggle", json={})
        client.post("/api/shopping/clear", json={})

        remaining = client.get("/api/shopping").json()
        assert len(remaining) == 1
        assert remaining[0]["purchased"] is False


class TestCalendar:
    def test_create_and_filter_by_range(self, client, house):
        client.post(
            "/api/events", json={"title": "Landlord visit", "starts_on": "2026-09-01"}
        )
        assert len(client.get("/api/events?start=2026-08-01&end=2026-09-30").json()) == 1
        assert client.get("/api/events?start=2026-10-01&end=2026-10-31").json() == []

    def test_all_day_when_no_time_given(self, client, house):
        event = client.post(
            "/api/events", json={"title": "Trip", "starts_on": "2026-09-01"}
        ).json()
        assert event["all_day"] is True

    def test_an_event_can_be_moved_and_renamed(self, client, house):
        event = client.post(
            "/api/events", json={"title": "Party", "starts_on": "2026-09-01"}
        ).json()
        edited = client.put(
            f"/api/events/{event['id']}",
            json={
                "title": "Housewarming",
                "starts_on": "2026-09-05",
                "starts_at": "19:30",
                "ends_at": "23:00",
                "location": "Roof",
                "notes": "Bring a chair",
            },
        ).json()
        assert edited["title"] == "Housewarming"
        assert edited["starts_on"] == "2026-09-05"
        assert edited["starts_at"] == "19:30:00"
        assert edited["location"] == "Roof"
        assert edited["all_day"] is False

    def test_clearing_the_time_makes_it_all_day_again(self, client, house):
        event = client.post(
            "/api/events",
            json={"title": "Party", "starts_on": "2026-09-01", "starts_at": "19:30"},
        ).json()
        assert event["all_day"] is False

        edited = client.put(
            f"/api/events/{event['id']}",
            json={"title": "Party", "starts_on": "2026-09-01", "starts_at": None},
        ).json()
        assert edited["all_day"] is True

    def test_editing_does_not_change_whose_event_it_is(self, client, house):
        """Who added it is who to ask about it -- an edit isn't a takeover."""
        event = client.post(
            "/api/events", json={"title": "Party", "starts_on": "2026-09-01"}
        ).json()
        client.post("/api/identify", json={"member_id": house[1]["id"]})

        edited = client.put(
            f"/api/events/{event['id']}",
            json={"title": "Party", "starts_on": "2026-09-02"},
        ).json()
        assert edited["created_by"]["name"] == "Gio"

    def test_an_edited_event_moves_between_date_ranges(self, client, house):
        event = client.post(
            "/api/events", json={"title": "Party", "starts_on": "2026-09-01"}
        ).json()
        client.put(
            f"/api/events/{event['id']}",
            json={"title": "Party", "starts_on": "2026-11-20"},
        )
        assert client.get("/api/events?start=2026-09-01&end=2026-09-30").json() == []
        assert len(client.get("/api/events?start=2026-11-01&end=2026-11-30").json()) == 1

    def test_editing_a_missing_event_is_a_404(self, client, house):
        res = client.put("/api/events/9999", json={"title": "x", "starts_on": "2026-09-01"})
        assert res.status_code == 404


class TestExpenses:
    def test_defaults_to_splitting_across_everyone(self, client, house):
        expense = client.post(
            "/api/expenses",
            json={
                "description": "Internet",
                "amount_cents": 6000,
                "paid_by_id": house[0]["id"],
            },
        ).json()
        assert len(expense["shares"]) == 3
        assert sum(s["share_cents"] for s in expense["shares"]) == 6000

    def test_ledger_nets_out(self, client, house):
        client.post(
            "/api/expenses",
            json={
                "description": "Internet",
                "amount_cents": 6000,
                "paid_by_id": house[0]["id"],
            },
        )
        ledger = client.get("/api/ledger").json()
        by_name = {b["member"]["name"]: b["net_cents"] for b in ledger["balances"]}
        assert by_name == {"Gio": 4000, "Sam": -2000, "Ali": -2000}
        assert len(ledger["transfers"]) == 2

    def test_split_between_a_subset(self, client, house):
        expense = client.post(
            "/api/expenses",
            json={
                "description": "Their pizza",
                "amount_cents": 2000,
                "paid_by_id": house[0]["id"],
                "split_between_ids": [house[1]["id"], house[2]["id"]],
            },
        ).json()
        assert {s["member"]["name"] for s in expense["shares"]} == {"Sam", "Ali"}
        ledger = client.get("/api/ledger").json()
        by_name = {b["member"]["name"]: b["net_cents"] for b in ledger["balances"]}
        assert by_name["Gio"] == 2000

    def test_settling_zeroes_the_ledger(self, client, house):
        client.post(
            "/api/expenses",
            json={
                "description": "Internet",
                "amount_cents": 6000,
                "paid_by_id": house[0]["id"],
            },
        )
        client.post("/api/expenses/settle", json={})
        ledger = client.get("/api/ledger").json()
        assert all(b["net_cents"] == 0 for b in ledger["balances"])
        assert ledger["transfers"] == []

    def test_rejects_a_zero_amount(self, client, house):
        res = client.post(
            "/api/expenses",
            json={"description": "x", "amount_cents": 0, "paid_by_id": house[0]["id"]},
        )
        assert res.status_code == 422


class TestBoard:
    def test_post_pin_and_delete(self, client, house):
        note = client.post("/api/notes", json={"body": "Wifi is hunter2"}).json()
        assert note["author"]["name"] == "Gio"

        pinned = client.post(f"/api/notes/{note['id']}/pin", json={}).json()
        assert pinned["pinned"] is True

        client.delete(f"/api/notes/{note['id']}")
        assert client.get("/api/notes").json() == []


class TestRecap:
    def test_empty_month_has_no_rows(self, client, house):
        recap = client.get("/api/recap?month=2020-01").json()
        assert recap["rows"] == []
        assert recap["totals"] == {"chores": 0, "items": 0, "spent_cents": 0}
        assert recap["label"] == "January 2020"

    def test_counts_chores_shopping_and_spending_together(
        self, client, house, monkeypatch
    ):
        chore = client.post("/api/chores", json={"name": "Dishes", "cadence": "daily"}).json()
        client.post(f"/api/chores/{chore['id']}/complete", json={})

        item = client.post("/api/shopping", json={"name": "Oat milk"}).json()
        client.post(f"/api/shopping/{item['id']}/toggle", json={})

        client.post(
            "/api/expenses",
            json={
                "description": "Internet",
                "amount_cents": 6000,
                "paid_by_id": house[0]["id"],
            },
        )

        recap = revealed_recap(client, monkeypatch)
        gio = next(r for r in recap["rows"] if r["member"]["name"] == "Gio")
        assert gio["chores_done"] == 1
        assert gio["items_bought"] == 1
        assert gio["paid_cents"] == 6000
        assert gio["owed_cents"] == 2000
        assert gio["net_cents"] == 4000
        assert recap["totals"]["spent_cents"] == 6000

    def test_repeated_chores_are_tallied_not_repeated(self, client, house, monkeypatch):
        chore = client.post("/api/chores", json={"name": "Dishes", "cadence": "daily"}).json()
        for _ in range(3):
            client.post(f"/api/chores/{chore['id']}/complete", json={})

        recap = revealed_recap(client, monkeypatch)
        gio = next(r for r in recap["rows"] if r["member"]["name"] == "Gio")
        assert gio["chores_done"] == 3
        assert gio["chore_names"] == [{"name": "Dishes", "count": 3}]

    def test_roommates_who_did_nothing_are_left_out(self, client, house, monkeypatch):
        chore = client.post("/api/chores", json={"name": "Dishes", "cadence": "daily"}).json()
        client.post(f"/api/chores/{chore['id']}/complete", json={})

        recap = revealed_recap(client, monkeypatch)
        assert [r["member"]["name"] for r in recap["rows"]] == ["Gio"]
        # ...but they're still available for a full-house view.
        assert len(recap["everyone"]) == 3

    def test_ordered_by_who_did_most(self, client, house, monkeypatch):
        chore = client.post("/api/chores", json={"name": "Dishes", "cadence": "daily"}).json()
        client.post(f"/api/chores/{chore['id']}/complete", json={"member_id": house[1]["id"]})
        client.post(f"/api/chores/{chore['id']}/complete", json={"member_id": house[1]["id"]})
        client.post(f"/api/chores/{chore['id']}/complete", json={"member_id": house[2]["id"]})

        rows = revealed_recap(client, monkeypatch)["rows"]
        assert [r["member"]["name"] for r in rows] == ["Sam", "Ali"]

    def test_other_months_are_not_counted(self, client, house):
        chore = client.post("/api/chores", json={"name": "Dishes", "cadence": "daily"}).json()
        client.post(f"/api/chores/{chore['id']}/complete", json={})

        last_month = client.get("/api/recap?month=2001-05").json()
        assert last_month["rows"] == []
        assert last_month["is_current"] is False

    def test_current_month_is_flagged(self, client, house):
        assert client.get("/api/recap").json()["is_current"] is True

    def test_rejects_a_malformed_month(self, client, house):
        assert client.get("/api/recap?month=nonsense").status_code == 400
        assert client.get("/api/recap?month=2026-13").status_code == 400


class TestSealedRecap:
    """A month in progress keeps its scores to itself."""

    def _done(self, client, house, times=1, member=None):
        chore = client.post(
            "/api/chores", json={"name": "Dishes", "cadence": "daily"}
        ).json()
        for _ in range(times):
            body = {"member_id": member} if member else {}
            client.post(f"/api/chores/{chore['id']}/complete", json=body)

    def test_this_month_is_sealed(self, client, house):
        self._done(client, house)
        recap = sealed_recap(client)
        assert recap["revealed"] is False

    def test_sealed_means_the_numbers_are_never_sent(self, client, house):
        """Withheld server-side, not hidden in the page -- otherwise anyone
        curious could read the standings out of the network tab all month."""
        self._done(client, house, times=3)
        recap = sealed_recap(client)
        assert recap["rows"] == []
        assert recap["everyone"] == []
        assert recap["awards"] == []
        assert "Gio" not in str(recap)

    def test_the_house_total_still_shows(self, client, house):
        """How the house is doing is not the same as who to blame for it."""
        self._done(client, house, times=3)
        assert sealed_recap(client)["totals"]["chores"] == 3

    def test_counts_down_to_the_reveal(self, client, house, monkeypatch):
        monkeypatch.setattr("app.service.today", lambda: dt.date(2026, 8, 12))
        recap = client.get("/api/recap?month=2026-08").json()
        assert recap["days_left"] == 20  # 31st is the last day; the 1st unseals

    def test_the_last_day_of_the_month_is_still_sealed(self, client, house, monkeypatch):
        monkeypatch.setattr("app.service.today", lambda: dt.date(2026, 8, 31))
        recap = client.get("/api/recap?month=2026-08").json()
        assert recap["revealed"] is False
        assert recap["days_left"] == 1

    def test_a_finished_month_is_open(self, client, house, monkeypatch):
        monkeypatch.setattr("app.service.today", lambda: dt.date(2026, 9, 1))
        recap = client.get("/api/recap?month=2026-08").json()
        assert recap["revealed"] is True
        assert recap["days_left"] == 0

    def test_a_month_that_has_not_started_is_sealed(self, client, house, monkeypatch):
        monkeypatch.setattr("app.service.today", lambda: dt.date(2026, 8, 12))
        assert client.get("/api/recap?month=2026-12").json()["revealed"] is False

    def test_awards_arrive_with_the_reveal(self, client, house, monkeypatch):
        self._done(client, house, times=3, member=house[0]["id"])
        self._done(client, house, times=2, member=house[1]["id"])
        self._done(client, house, times=1, member=house[2]["id"])

        awards = revealed_recap(client, monkeypatch)["awards"]
        assert [(a["key"], a["winners"][0]["member"]["name"]) for a in awards] == [
            ("mvr", "Gio"),
            ("shlom", "Sam"),
            ("lvr", "Ali"),
        ]

    def test_a_quiet_month_hands_out_nothing(self, client, house, monkeypatch):
        assert revealed_recap(client, monkeypatch)["awards"] == []


class TestHealth:
    def test_reports_which_database_is_behind_it(self, client):
        """The one thing you can't tell from outside until data goes missing."""
        body = client.get("/healthz").json()
        assert body["ok"] is True
        assert body["database"] in {"sqlite", "postgres"}

    def test_never_leaks_the_connection_string(self, client):
        assert "://" not in str(client.get("/healthz").json())

    def test_needs_no_passcode(self, client, monkeypatch):
        """A health check that 401s is no use to a host's uptime probe."""
        monkeypatch.setattr("app.auth.AUTH_DISABLED", False)
        monkeypatch.setattr("app.auth.config.HOUSE_PASSCODE", "kachow")
        assert client.get("/healthz").status_code == 200


class TestAuth:
    def test_open_house_when_no_passcode_is_set(self, client):
        # With no HOUSE_PASSCODE configured the door is open -- the localhost
        # default, refused in production by main.py's startup guard.
        assert client.get("/api/me").json()["authenticated"] is True

    def test_locked_house_rejects_a_bad_passcode(self, client, monkeypatch):
        monkeypatch.setattr("app.auth.AUTH_DISABLED", False)
        monkeypatch.setattr("app.config.HOUSE_PASSCODE", "letmein")
        assert client.post("/api/login", json={"passcode": "nope"}).status_code == 401
        assert client.get("/api/chores").status_code == 401

    def test_correct_passcode_opens_the_door(self, client, monkeypatch):
        monkeypatch.setattr("app.auth.AUTH_DISABLED", False)
        monkeypatch.setattr("app.config.HOUSE_PASSCODE", "letmein")
        assert client.post("/api/login", json={"passcode": "letmein"}).status_code == 200
        assert client.get("/api/chores").status_code == 200

    def test_unknown_api_path_is_a_404_not_the_app_shell(self, client):
        res = client.get("/api/nope")
        assert res.status_code == 404
        assert res.json()["detail"] == "Not found"
