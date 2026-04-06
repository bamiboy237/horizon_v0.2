"""Unit tests for the opportunities seed loader."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

from data import seed_opportunity as seed_script


class FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakeConnection:
    def __init__(self, *, existing_urls: set[str] | None = None) -> None:
        self.existing_urls = existing_urls or set()
        self.executions: list[tuple[str, tuple[object, ...]]] = []

    async def fetchval(self, query: str, normalized_url: str) -> int | None:
        assert "FROM opportunities" in query
        return 1 if normalized_url in self.existing_urls else None

    async def execute(self, query: str, *params: object) -> str:
        assert "INSERT INTO opportunities" in query
        self.executions.append((query, params))
        self.existing_urls.add(str(params[1]))
        return "INSERT 0 1"

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()


class FakeAcquire:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.closed = False

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.connection)

    async def close(self) -> None:
        self.closed = True


def test_load_opportunities_reads_wrapped_dataset() -> None:
    json_path = Path(__file__).resolve().parents[3] / "data" / "opportunities.json"

    opportunities = seed_script.load_opportunities(json_path)

    assert len(opportunities) == 150
    assert opportunities[0]["title"] == "Google STEP Internship"


def test_normalize_source_url_canonicalizes_common_variants() -> None:
    normalized = seed_script.normalize_source_url("HTTPS://Example.com/path/?q=1#fragment")

    assert normalized == "https://example.com/path?q=1"


def test_normalize_opportunity_builds_insertable_payload() -> None:
    record = {
        "source_url": "https://Example.com/path/",
        "title": "Example Opportunity",
        "organization": "Example Org",
        "opportunity_type": "internship",
        "deadline": "2026-01-15T23:59:59Z",
        "major_requirements": ["Computer Science"],
        "required_materials": ["resume"],
        "demographic_requirements": {"group": ["students"]},
        "gpa_minimum": 3.0,
    }

    payload = seed_script.normalize_opportunity(record, verified_at=datetime(2026, 4, 5, tzinfo=UTC))

    assert payload["normalized_url"] == "https://example.com/path"
    assert payload["deadline"] == datetime(2026, 1, 15, 23, 59, 59, tzinfo=UTC)
    assert payload["embedding_model"] == "text-embedding-004"
    assert payload["major_requirements"] == ["Computer Science"]
    assert payload["demographic_requirements"] == {"group": ["students"]}
    assert record["source_url"] == "https://Example.com/path/"


def test_dedupe_opportunities_keeps_first_normalized_url() -> None:
    first = {"normalized_url": "https://example.com/a", "title": "First"}
    second = {"normalized_url": "https://example.com/a", "title": "Second"}

    unique_records, skipped = seed_script.dedupe_opportunities([first, second])

    assert unique_records == [first]
    assert skipped == 1


@pytest.mark.asyncio
async def test_seed_opportunities_processes_all_150_records(monkeypatch: pytest.MonkeyPatch) -> None:
    json_path = Path(__file__).resolve().parents[3] / "data" / "opportunities.json"
    pool = FakePool(FakeConnection())

    monkeypatch.setattr(seed_script, "load_settings", lambda: seed_script.SeedSettings(database_url="postgresql://example/db"))

    async def fake_create_pool(**kwargs: object) -> FakePool:
        return pool

    monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)

    stats = await seed_script.seed_opportunities(json_path)

    assert stats == {"total": 150, "inserted": 150, "updated": 0, "skipped": 0, "errors": 0}
    assert len(pool.connection.executions) == 150
    assert pool.closed is True