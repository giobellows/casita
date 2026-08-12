"""End-to-end API behaviour."""

from __future__ import annotations

import datetime as dt


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

    def test_completion_is_credited_quietly_to_the_browsers_member(self, client, house):
        chore = self._rotating(client, house)
        client.post(f"/api/chores/{chore['id']}/complete", json={})
        # Attribution never appears in the live views, only in the recap.
        rows = client.get("/api/recap").json()["rows"]
        assert [(r["member"]["name"], r["chores_done"]) for r in rows] == [("Gio", 1)]

    def test_explicit_doer_overrides_the_cookie(self, client, house):
        chore = self._rotating(client, house)
        client.post(
            f"/api/chores/{chore['id']}/complete", json={"member_id": house[2]["id"]}
        )
        rows = client.get("/api/recap").json()["rows"]
        assert [(r["member"]["name"], r["chores_done"]) for r in rows] == [("Ali", 1)]

    def test_chores_are_unassigned_unless_asked_for(self, client, house):
        """Assignment is available but never forced -- the default is nobody."""
        chore = client.post("/api/chores", json={"name": "Water the plants"}).json()
        assert chore["assignee"] is None
        assert chore["rotation_mode"] == "anyone"
        assert chore["rotation"] == []

    def test_an_unassigned_chore_can_still_be_completed(self, client, house):
        chore = client.post("/api/chores", json={"name": "Sweep", "cadence": "weekly"}).json()
        done = client.post(f"/api/chores/{chore['id']}/complete", json={}).json()
        assert done["assignee"] is None
        rows = client.get("/api/recap").json()["rows"]
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

    def test_undoes_only_the_most_recent(self, client, house):
        chore = client.post("/api/chores", json={"name": "Dishes", "cadence": "daily"}).json()
        client.post(f"/api/chores/{chore['id']}/complete", json={"member_id": house[0]["id"]})
        client.post(f"/api/chores/{chore['id']}/complete", json={"member_id": house[1]["id"]})

        client.post(f"/api/chores/{chore['id']}/undo", json={})
        rows = client.get("/api/recap").json()["rows"]
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

    def test_counts_chores_shopping_and_spending_together(self, client, house):
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

        recap = client.get("/api/recap").json()
        gio = next(r for r in recap["rows"] if r["member"]["name"] == "Gio")
        assert gio["chores_done"] == 1
        assert gio["items_bought"] == 1
        assert gio["paid_cents"] == 6000
        assert gio["owed_cents"] == 2000
        assert gio["net_cents"] == 4000
        assert recap["totals"]["spent_cents"] == 6000

    def test_repeated_chores_are_tallied_not_repeated(self, client, house):
        chore = client.post("/api/chores", json={"name": "Dishes", "cadence": "daily"}).json()
        for _ in range(3):
            client.post(f"/api/chores/{chore['id']}/complete", json={})

        recap = client.get("/api/recap").json()
        gio = next(r for r in recap["rows"] if r["member"]["name"] == "Gio")
        assert gio["chores_done"] == 3
        assert gio["chore_names"] == [{"name": "Dishes", "count": 3}]

    def test_roommates_who_did_nothing_are_left_out(self, client, house):
        chore = client.post("/api/chores", json={"name": "Dishes", "cadence": "daily"}).json()
        client.post(f"/api/chores/{chore['id']}/complete", json={})

        recap = client.get("/api/recap").json()
        assert [r["member"]["name"] for r in recap["rows"]] == ["Gio"]
        # ...but they're still available for a full-house view.
        assert len(recap["everyone"]) == 3

    def test_ordered_by_who_did_most(self, client, house):
        chore = client.post("/api/chores", json={"name": "Dishes", "cadence": "daily"}).json()
        client.post(f"/api/chores/{chore['id']}/complete", json={"member_id": house[1]["id"]})
        client.post(f"/api/chores/{chore['id']}/complete", json={"member_id": house[1]["id"]})
        client.post(f"/api/chores/{chore['id']}/complete", json={"member_id": house[2]["id"]})

        rows = client.get("/api/recap").json()["rows"]
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
