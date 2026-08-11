"""Test fixtures: a throwaway in-memory database per test."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models import Base


@pytest.fixture
def client(monkeypatch):
    # The suite must not care whether the developer has a HOUSE_PASSCODE in
    # their local .env, so every test starts with the door open. The auth tests
    # close it again explicitly.
    monkeypatch.setattr("app.auth.AUTH_DISABLED", True)

    # StaticPool keeps every connection pointed at the same in-memory database;
    # without it each checkout would get a fresh, empty one.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def house(client):
    """Three roommates, and the client identified as the first of them."""
    members = [
        client.post("/api/members", json={"name": name, "emoji": emoji}).json()
        for name, emoji in (("Gio", "🐙"), ("Sam", "🦊"), ("Ali", "🐢"))
    ]
    client.post("/api/identify", json={"member_id": members[0]["id"]})
    return members
