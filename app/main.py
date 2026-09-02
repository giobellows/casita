"""Application entrypoint.

Run locally with:

    .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

Binding 0.0.0.0 rather than 127.0.0.1 is what lets phones on the same wifi reach
it during development.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import auth, config, models
from app.api import api, public
from app.db import engine

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _guard_production() -> None:
    """Refuse to serve the whole house to the internet with no lock on the door."""
    if not config.IS_PRODUCTION:
        return
    problems = []
    if auth.AUTH_DISABLED:
        problems.append("HOUSE_PASSCODE is unset -- anyone with the URL gets in")
    if config.SECRET_KEY.startswith("dev-insecure"):
        problems.append("SECRET_KEY is still the development default")
    if problems:
        raise RuntimeError(
            "Refusing to start in production:\n  - " + "\n  - ".join(problems)
        )


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    _guard_production()
    # No migration tool: the schema is small and additive, and create_all is
    # enough until it isn't. Swap in Alembic the first time a column changes.
    models.Base.metadata.create_all(engine)
    yield


app = FastAPI(title=f"{config.HOUSE_NAME} — house app", lifespan=lifespan)
app.include_router(public)
app.include_router(api)


@app.get("/healthz")
def healthz():
    """Liveness, plus which database is actually behind it.

    `database` reports the backend and never the URL. A host where DATABASE_URL
    was meant to be set but wasn't falls back to SQLite on the container's disk,
    which behaves identically from outside right up until a redeploy wipes it --
    so this is the one thing worth being able to check without a shell.
    """
    return {
        "ok": True,
        "database": "sqlite" if config.IS_SQLITE else "postgres",
    }


@app.exception_handler(404)
async def spa_fallback(request: Request, _exc):
    """Unknown API paths stay 404s; anything else hands back the app shell."""
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(STATIC_DIR / "index.html")


# Mounted last so it never shadows the API routes above.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
