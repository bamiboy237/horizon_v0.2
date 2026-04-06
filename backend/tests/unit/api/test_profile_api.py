"""Unit tests for the profile API endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.api import profile as profile_api
from app.core.security import get_current_user
from app.models.schemas import AuthenticatedUser
from app.services import profile as profile_service


class FakeConnection:
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self.rows = rows

    async def fetchrow(self, query: str, *params: object) -> dict[str, object] | None:
        user_id = str(params[0])
        if query.lstrip().upper().startswith("SELECT"):
            row = self.rows.get(user_id)
            return None if row is None else dict(row)

        if query.lstrip().upper().startswith("UPDATE"):
            row = self.rows.get(user_id)
            if row is None:
                return None

            updated_row = dict(row)
            value_index = 1
            for column in profile_service.PROFILE_EDITABLE_COLUMNS:
                if f"{column} =" in query:
                    updated_row[column] = params[value_index]
                    value_index += 1

            updated_row["updated_at"] = datetime(2026, 4, 5, tzinfo=UTC)
            self.rows[user_id] = updated_row
            return dict(updated_row)

        raise AssertionError(f"Unexpected query: {query}")

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()


class FakeAcquire:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakePool:
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self.connection = FakeConnection(rows)

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.connection)


@pytest_asyncio.fixture
async def profile_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, dict[str, object]]]]:
    rows: dict[str, dict[str, object]] = {
        "user_123": {
            "id": "user_123",
            "email": "student@example.com",
            "full_name": "Ada Lovelace",
            "institution": "Bletchley Park",
            "institution_type": "University",
            "major": "Mathematics",
            "cip_code": "27.0101",
            "gpa": 3.95,
            "graduation_year": 2027,
            "citizenship": "US",
            "state_residence": "CA",
            "first_generation": True,
            "ethnicity": ["Women in STEM"],
            "goals": ["Scholarships"],
            "interests": ["math", "research"],
            "career_aspirations": ["scientist"],
            "onboarding_complete": False,
            "profile_embedding": [0.1, 0.2],
            "interaction_embedding": [0.3, 0.4],
            "embedding_model": "text-embedding-004",
            "email_digest_enabled": True,
            "email_digest_frequency": "weekly",
            "created_at": datetime(2026, 4, 5, tzinfo=UTC),
            "updated_at": datetime(2026, 4, 5, tzinfo=UTC),
        }
    }
    pool = FakePool(rows)
    monkeypatch.setattr(profile_service, "get_db_pool", lambda: pool)

    app = FastAPI()
    app.include_router(profile_api.router)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="user_123")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, rows


@pytest.mark.asyncio
async def test_get_profile_returns_row(profile_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]]) -> None:
    client, _ = profile_client

    response = await client.get("/api/profile")

    assert response.status_code == 200
    assert response.json()["id"] == "user_123"
    assert response.json()["email"] == "student@example.com"


@pytest.mark.asyncio
async def test_get_profile_returns_404_when_local_profile_missing(
    profile_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
) -> None:
    client, rows = profile_client
    rows.pop("user_123")

    response = await client.get("/api/profile")

    assert response.status_code == 404
    assert response.json()["detail"] == "Authenticated user profile was not found."


@pytest.mark.asyncio
async def test_patch_profile_updates_allowed_fields_and_preserves_others(
    profile_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
) -> None:
    client, rows = profile_client

    response = await client.patch(
        "/api/profile",
        json={
            "full_name": "Grace Hopper",
            "gpa": 4.0,
            "email_digest_enabled": False,
            "onboarding_complete": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Grace Hopper"
    assert body["gpa"] == 4.0
    assert body["institution"] == "Bletchley Park"
    assert body["email_digest_enabled"] is False
    assert body["onboarding_complete"] is True
    assert rows["user_123"]["institution"] == "Bletchley Park"


@pytest.mark.asyncio
async def test_patch_profile_downgrades_onboarding_when_required_fields_are_removed(
    profile_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
) -> None:
    client, rows = profile_client
    rows["user_123"]["onboarding_complete"] = True

    response = await client.patch("/api/profile", json={"institution": None})

    assert response.status_code == 200
    assert response.json()["institution"] is None
    assert response.json()["onboarding_complete"] is False
    assert rows["user_123"]["onboarding_complete"] is False


@pytest.mark.asyncio
async def test_patch_profile_ignores_null_values_for_concrete_fields(
    profile_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
) -> None:
    client, rows = profile_client

    response = await client.patch(
        "/api/profile",
        json={
            "email_digest_enabled": None,
            "email_digest_frequency": None,
            "first_generation": None,
            "onboarding_complete": None,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["email_digest_enabled"] is True
    assert body["email_digest_frequency"] == "weekly"
    assert body["first_generation"] is True
    assert body["onboarding_complete"] is False
    assert rows["user_123"]["email_digest_enabled"] is True


@pytest.mark.asyncio
async def test_patch_profile_rejects_premature_onboarding_completion(
    profile_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
) -> None:
    client, rows = profile_client
    rows["user_123"].pop("institution")

    response = await client.patch("/api/profile", json={"onboarding_complete": True})

    assert response.status_code == 422
    assert "Complete the required profile fields" in response.json()["detail"]


@pytest.mark.asyncio
async def test_put_profile_updates_profile_like_patch(
    profile_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
) -> None:
    client, _ = profile_client

    response = await client.put("/api/profile", json={"full_name": "Katherine Johnson"})

    assert response.status_code == 200
    assert response.json()["full_name"] == "Katherine Johnson"


@pytest.mark.asyncio
async def test_patch_profile_rejects_server_managed_fields(
    profile_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
) -> None:
    client, _ = profile_client

    response = await client.patch(
        "/api/profile",
        json={
            "id": "malicious",
            "email": "bad@example.com",
            "profile_embedding": [1.0, 2.0],
            "updated_at": "2026-04-05T00:00:00Z",
        },
    )

    assert response.status_code == 422
    error_locations = {tuple(error["loc"]) for error in response.json()["detail"]}
    assert ("body", "id") in error_locations
    assert ("body", "email") in error_locations
    assert ("body", "profile_embedding") in error_locations
    assert ("body", "updated_at") in error_locations


@pytest.mark.asyncio
async def test_patch_profile_returns_404_when_local_profile_missing(
    profile_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
) -> None:
    client, rows = profile_client
    rows.pop("user_123")

    response = await client.patch("/api/profile", json={"full_name": "Grace Hopper"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Authenticated user profile was not found."