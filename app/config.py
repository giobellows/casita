"""Configuration, read from the environment with sane local defaults."""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _database_url() -> str:
    """Normalise whatever DATABASE_URL a host hands us into a SQLAlchemy 2 URL."""
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return f"sqlite:///{BASE_DIR / 'casita.db'}"
    # Render, Heroku and Railway all still hand out the legacy postgres:// scheme,
    # which SQLAlchemy 2 rejects outright. Pin the driver while we're here.
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def _timezone() -> ZoneInfo:
    """The house's timezone. Everything date-shaped is computed against this."""
    name = os.getenv("TIMEZONE", "America/New_York").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


DATABASE_URL = _database_url()
IS_SQLITE = DATABASE_URL.startswith("sqlite")

# One shared passcode for the whole house. Empty means the door is open, which is
# convenient on localhost -- main.py refuses to boot that way in production.
HOUSE_PASSCODE = os.getenv("HOUSE_PASSCODE", "").strip()
SECRET_KEY = os.getenv(
    "SECRET_KEY", "dev-insecure-key-do-not-use-in-production"
).strip()

HOUSE_NAME = os.getenv("HOUSE_NAME", "Casita").strip() or "Casita"

SESSION_COOKIE = "casita_session"
# Which roommate this browser belongs to. Deliberately separate from the session
# cookie: signing in gets you into the house, picking a name says who you are.
MEMBER_COOKIE = "casita_member"
# A year, so a phone home-screen shortcut never bounces anyone to a login wall.
SESSION_MAX_AGE = 60 * 60 * 24 * 365

HOUSE_TZ = _timezone()

# Render sets RENDER; most PaaS set PORT. Used only to decide cookie security.
IS_PRODUCTION = bool(os.getenv("RENDER") or os.getenv("PRODUCTION"))
