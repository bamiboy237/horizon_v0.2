"""Unit tests for profile service helpers and update logic."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.exceptions import IncompleteProfileError
from app.services import profile as profile_service


class FakeConnection:
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, query: str, *params: object) -> dict[str, object] | None:
        self.calls.append((query, params))

        if query.lstrip().upper().startswith("SELECT"):
            user_id = str(params[0])
            row = self.rows.get(user_id)
            return None if row is None else dict(row)

        if query.lstrip().upper().startswith("UPDATE"):
            user_id = str(params[0])
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


@pytest.fixture
def fake_profile_pool(monkeypatch: pytest.MonkeyPatch) -> tuple[FakePool, dict[str, dict[str, object]]]:
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
    return pool, rows


@pytest.mark.asyncio
async def test_get_profile_by_user_id_returns_full_row(fake_profile_pool: tuple[FakePool, dict[str, dict[str, object]]]) -> None:
    pool, rows = fake_profile_pool

    profile = await profile_service.get_profile_by_user_id("user_123")

    assert profile is not None
    assert profile["email"] == "student@example.com"
    assert profile["profile_embedding"] == [0.1, 0.2]
    query, params = pool.connection.calls[0]
    assert "FROM profiles" in query
    assert params == ("user_123",)
    assert profile == rows["user_123"]


@pytest.mark.asyncio
async def test_get_profile_by_user_id_returns_none_when_missing(
    fake_profile_pool: tuple[FakePool, dict[str, dict[str, object]]],
) -> None:
    pool, _ = fake_profile_pool

    profile = await profile_service.get_profile_by_user_id("missing")

    assert profile is None
    query, params = pool.connection.calls[0]
    assert "FROM profiles" in query
    assert params == ("missing",)


@pytest.mark.asyncio
async def test_update_profile_by_user_id_updates_only_allowed_fields_and_returns_row(
    fake_profile_pool: tuple[FakePool, dict[str, dict[str, object]]],
) -> None:
    pool, rows = fake_profile_pool

    updated = await profile_service.update_profile_by_user_id(
        "user_123",
        {
            "full_name": "Grace Hopper",
            "gpa": 4.0,
            "onboarding_complete": True,
        },
    )

    assert updated is not None
    assert updated["full_name"] == "Grace Hopper"
    assert updated["gpa"] == 4.0
    assert updated["institution"] == "Bletchley Park"
    assert updated["updated_at"] == datetime(2026, 4, 5, tzinfo=UTC)
    assert rows["user_123"]["institution"] == "Bletchley Park"

    query, params = pool.connection.calls[-1]
    assert "UPDATE profiles" in query
    assert "updated_at = NOW()" in query
    assert params == ("user_123", "Grace Hopper", 4.0, True)


@pytest.mark.asyncio
async def test_update_profile_by_user_id_ignores_null_values_for_concrete_fields(
    fake_profile_pool: tuple[FakePool, dict[str, dict[str, object]]],
) -> None:
    pool, rows = fake_profile_pool

    updated = await profile_service.update_profile_by_user_id(
        "user_123",
        {
            "email_digest_enabled": None,
            "email_digest_frequency": None,
            "first_generation": None,
            "onboarding_complete": None,
        },
    )

    assert updated is not None
    assert updated["email_digest_enabled"] is True
    assert updated["email_digest_frequency"] == "weekly"
    assert updated["first_generation"] is True
    assert updated["onboarding_complete"] is False
    assert rows["user_123"]["email_digest_enabled"] is True

    query, params = pool.connection.calls[-1]
    assert "email_digest_enabled =" not in query
    assert "email_digest_frequency =" not in query
    assert "first_generation =" not in query
    assert "onboarding_complete =" not in query
    assert params == ("user_123",)


@pytest.mark.asyncio
async def test_update_profile_by_user_id_downgrades_onboarding_when_profile_becomes_incomplete(
    fake_profile_pool: tuple[FakePool, dict[str, dict[str, object]]],
) -> None:
    _, rows = fake_profile_pool

    updated = await profile_service.update_profile_by_user_id("user_123", {"institution": None})

    assert updated is not None
    assert updated["institution"] is None
    assert updated["onboarding_complete"] is False
    assert rows["user_123"]["onboarding_complete"] is False


@pytest.mark.asyncio
async def test_update_profile_by_user_id_rejects_premature_onboarding_completion(
    fake_profile_pool: tuple[FakePool, dict[str, dict[str, object]]],
) -> None:
    pool, rows = fake_profile_pool
    rows["user_123"].pop("institution")

    with pytest.raises(IncompleteProfileError) as exc_info:
        await profile_service.update_profile_by_user_id("user_123", {"onboarding_complete": True})

    assert "Complete the required profile fields" in str(exc_info.value)
    assert len(pool.connection.calls) == 1


@pytest.mark.asyncio
async def test_update_profile_by_user_id_returns_none_when_missing(
    fake_profile_pool: tuple[FakePool, dict[str, dict[str, object]]],
) -> None:
    pool, _ = fake_profile_pool

    updated = await profile_service.update_profile_by_user_id("missing", {"full_name": "New Name"})

    assert updated is None
    query, params = pool.connection.calls[-1]
    assert "SELECT *" in query
    assert "UPDATE profiles" not in query
    assert params[0] == "missing"