"""Engine, session factory, and the FastAPI session dependency."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import config

# check_same_thread is a SQLite-only knob: FastAPI runs sync endpoints in a
# threadpool, so a connection will legitimately be touched by several threads.
_connect_args = {"check_same_thread": False} if config.IS_SQLITE else {}

engine = create_engine(
    config.DATABASE_URL,
    connect_args=_connect_args,
    pool_pre_ping=not config.IS_SQLITE,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a session that always gets closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
