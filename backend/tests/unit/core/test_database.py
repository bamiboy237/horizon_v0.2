"""Unit tests for database pool lifecycle helpers."""

from __future__ import annotations

from typing import Any

import pytest

from app.core import database
from app.core.config import Settings


def build_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_name": "horizon-backend",
        "app_env": "development",
        "log_level": "INFO",
        "database_url": None,
        "database_pool_min_size": 1,
        "database_pool_max_size": 10,
        "database_command_timeout": 30.0,
        "clerk_secret_key": None,
        "clerk_webhook_signing_secret": None,
        "clerk_authorized_parties": [],
    }
    values.update(overrides)
    return Settings.model_construct(**values)


class FakeConnection:
    def __init__(self, *, fetchval_result: int | None = 1, should_fail: bool = False) -> None:
        self.fetchval_result = fetchval_result
        self.should_fail = should_fail
        self.fetchval_queries: list[str] = []

    async def fetchval(self, query: str) -> int | None:
        self.fetchval_queries.append(query)

        if self.should_fail:
            raise RuntimeError("connection unavailable")

        return self.fetchval_result


class FakeAcquireContext:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakePool:
    def __init__(self, connection: FakeConnection | None = None) -> None:
        self.connection = connection or FakeConnection()
        self.acquire_calls = 0
        self.closed = False

    def acquire(self) -> FakeAcquireContext:
        self.acquire_calls += 1
        return FakeAcquireContext(self.connection)

    async def close(self) -> None:
        self.closed = True


async def test_initialize_db_pool_requires_database_url() -> None:
    settings = build_settings()

    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        await database.initialize_db_pool(settings)

    assert database.is_db_configured() is False


async def test_initialize_db_pool_creates_and_caches_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pool = FakePool()
    create_pool_calls: list[dict[str, object]] = []

    async def fake_create_pool(**kwargs: object) -> FakePool:
        create_pool_calls.append(dict(kwargs))
        return fake_pool

    monkeypatch.setattr(database.asyncpg, "create_pool", fake_create_pool)

    settings = build_settings(
        database_url="postgresql://localhost/example",
        database_pool_min_size=2,
        database_pool_max_size=5,
        database_command_timeout=12.5,
    )

    pool = await database.initialize_db_pool(settings)
    cached_pool = await database.initialize_db_pool(settings)

    assert pool is fake_pool
    assert cached_pool is fake_pool
    assert database.get_db_pool() is fake_pool
    assert database.is_db_configured() is True
    assert database.get_last_connection_error() is None
    assert create_pool_calls == [
        {
            "dsn": "postgresql://localhost/example",
            "min_size": 2,
            "max_size": 5,
            "command_timeout": 12.5,
        }
    ]


async def test_initialize_db_pool_records_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_create_pool(**kwargs: object) -> FakePool:
        raise RuntimeError("boom")

    monkeypatch.setattr(database.asyncpg, "create_pool", fake_create_pool)

    settings = build_settings(database_url="postgresql://localhost/example")

    with pytest.raises(RuntimeError, match="boom"):
        await database.initialize_db_pool(settings)

    assert database.is_db_configured() is True
    assert database.get_last_connection_error() == "boom"


async def test_close_db_pool_resets_state() -> None:
    fake_pool = FakePool()
    database._pool = fake_pool

    await database.close_db_pool()

    assert fake_pool.closed is True
    assert database._pool is None


def test_get_db_pool_raises_when_uninitialized() -> None:
    with pytest.raises(RuntimeError, match="Database pool has not been initialized"):
        database.get_db_pool()


async def test_check_db_connection_reports_success_and_failure() -> None:
    healthy_pool = FakePool(connection=FakeConnection(fetchval_result=1))
    database._pool = healthy_pool

    assert await database.check_db_connection() is True
    assert healthy_pool.connection.fetchval_queries == ["SELECT 1;"]

    failing_pool = FakePool(connection=FakeConnection(should_fail=True))
    database._pool = failing_pool

    assert await database.check_db_connection() is False