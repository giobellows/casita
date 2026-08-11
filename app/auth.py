"""Shared-passcode auth plus a lightweight "which roommate is this" cookie.

Two separate ideas, deliberately kept apart:

  * The **session** cookie says this browser is allowed in the house. It's
    granted by the one shared passcode everyone knows.
  * The **member** cookie says which roommate this browser belongs to, so
    actions can be attributed. It is not a security boundary -- anyone already
    inside could claim to be anyone else -- it's a convenience, the digital
    equivalent of writing your initials on the whiteboard.

Real per-user accounts would mean signups, password resets and forgotten
passwords, which is a lot of machinery for four people who share a kitchen.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app import config

_session_serializer = URLSafeTimedSerializer(config.SECRET_KEY, salt="casita-session-v1")
_member_serializer = URLSafeTimedSerializer(config.SECRET_KEY, salt="casita-member-v1")

AUTH_DISABLED = not config.HOUSE_PASSCODE


def check_passcode(candidate: str) -> bool:
    """Constant-time comparison, so the passcode can't be probed by timing."""
    if AUTH_DISABLED:
        return True
    return hmac.compare_digest(candidate.encode(), config.HOUSE_PASSCODE.encode())


def issue_session() -> str:
    return _session_serializer.dumps("house")


def session_is_valid(token: str | None) -> bool:
    if not token:
        return False
    try:
        _session_serializer.loads(token, max_age=config.SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return False
    return True


def is_authenticated(request: Request) -> bool:
    if AUTH_DISABLED:
        return True
    return session_is_valid(request.cookies.get(config.SESSION_COOKIE))


def require_auth(request: Request) -> None:
    """FastAPI dependency: 401 unless the request carries a valid session."""
    if not is_authenticated(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in"
        )


def issue_member(member_id: int) -> str:
    return _member_serializer.dumps(member_id)


def current_member_id(request: Request) -> int | None:
    """Which roommate this browser claims to be, or None if they haven't said."""
    raw = request.cookies.get(config.MEMBER_COOKIE)
    if not raw:
        return None
    try:
        value = _member_serializer.loads(raw, max_age=config.SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return value if isinstance(value, int) else None


def set_cookie(response, name: str, value: str) -> None:
    """Set a long-lived cookie, hardened when we're actually deployed."""
    response.set_cookie(
        name,
        value,
        max_age=config.SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=config.IS_PRODUCTION,
    )
