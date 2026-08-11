# Casita 🏡

A small house app for roommates: chores, a shopping list, a shared calendar,
split costs, and a house board. Works in any browser and installs to a phone
home screen like a native app.

## Running it locally

```bash
.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Then open <http://localhost:8000>.

Binding `0.0.0.0` (rather than `127.0.0.1`) is what lets phones on the same wifi
reach it — find your machine's LAN address with `ipconfig getifaddr en0` and
visit `http://<that-address>:8000` from your phone.

## Getting it on everyone's phone

Once it's reachable, open it in Safari or Chrome on the phone and choose **Add to
Home Screen**. It then launches full-screen with its own icon, no browser
chrome, and stays signed in for a year.

For access from anywhere — not just the house wifi — deploy it. Any host that
runs Python works; the config already understands the `DATABASE_URL` that
Render, Railway and Heroku hand out, and upgrades the legacy `postgres://`
scheme that SQLAlchemy 2 rejects. Set `HOUSE_PASSCODE` and `SECRET_KEY` and the
app boots against Postgres instead of the local SQLite file.

Start command for a PaaS:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Configuration

Copy `.env.example` to `.env`. Everything has a working local default except the
two secrets.

| Variable | What it does |
| --- | --- |
| `HOUSE_PASSCODE` | The one passcode everyone types once per device. Empty means the door is open — fine on localhost, refused in production. |
| `SECRET_KEY` | Signs session cookies. Generate with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `HOUSE_NAME` | Shown in the header and browser tab. |
| `TIMEZONE` | Drives what "today" means for chore due dates. |
| `DATABASE_URL` | Unset for local SQLite; set by your host for Postgres. |

The app refuses to start in production if the passcode is missing or the secret
key is still the development default.

## How it works

**Signing in** happens twice, deliberately. The shared passcode gets a browser
into the house; picking your name from a list says who you are. There are no
individual accounts, no signups and no password resets — a lot of machinery for
four people who share a kitchen.

**Chores** repeat daily, weekly or monthly (every *N* of those, so "every 2
weeks" works), or are one-offs that archive when done. Completing one rolls the
due date forward; a chore that went ignored for a month catches up past today
rather than reappearing as still overdue.

Assigning a chore is **optional**. By default nobody owns it — anyone can tick
it off. If you do want turns, a chore can be pinned to one person or given a
rotation ring, and completing it passes the baton to the next roommate.

**Attribution is recorded but not displayed.** Ticking a chore or buying an item
quietly notes who did it; the day-to-day lists stay anonymous so the app reads
like a shared list rather than a scoreboard. Those numbers surface in one place:
the **Recap** tab, which shows who did what month by month, with past months
browsable. Roommates who did nothing that month are left off — a row of zeroes
for someone who was travelling reads as an accusation, not information.

**Money** is stored in integer cents, never floats. Bills split evenly with the
leftover pennies distributed deterministically, so shares always sum to exactly
the total. The ledger nets everything out and suggests the shortest set of
payments to square up.

## Shipping an update

Installed home-screen apps hold onto cached assets, so after changing
`app.js` or `styles.css`, bump the `?v=` numbers in `app/static/index.html` and
the matching `CACHE` name and `SHELL` entries in `app/static/sw.js`. The service
worker deletes every other cache on activation, so every phone picks up the new
version the next time it's opened. Backend changes need no bump.

## Tests

```bash
.venv/bin/python -m pytest -q
```

73 tests covering the date arithmetic (month-end clamping, catch-up, rotation
wrap-around), the money splitting (shares always sum to the total, settle-up
clears every balance), and the API end to end.

## Layout

```
app/
  main.py        entrypoint, static mounting, production guard
  api.py         HTTP routes — thin, delegates to service.py
  service.py     business operations; anything touching >1 table
  models.py      ORM models
  schemas.py     request/response shapes
  rotation.py    pure date + turn-taking logic
  money.py       pure splitting + settle-up logic
  auth.py        passcode session, member identity
  static/        the frontend — no build step, no dependencies
tests/
```
