"""Unit tests for the FastAPI application factory and lifespan setup."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

import pytest

from app.core.config import Settings
import main


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


def test_create_app_registers_health_route() -> None:
    app = main.create_app()
    paths = {route.path for route in app.router.routes}

    assert app.title == "Horizon v0.2 Backend"
    assert "/health" in paths


async def test_lifespan_initializes_and_closes_database(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    settings = build_settings(database_url="postgresql://localhost/example", app_name="horizon", app_env="test")

    async def fake_initialize_db_pool(received_settings: Settings) -> None:
        calls.append(f"initialize:{received_settings.database_url}")

    async def fake_close_db_pool() -> None:
        calls.append("close")

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(
        main,
        "configure_logging",
        lambda log_level, app_env: calls.append(f"logging:{log_level}:{app_env}"),
    )
    monkeypatch.setattr(main, "initialize_db_pool", fake_initialize_db_pool)
    monkeypatch.setattr(main, "close_db_pool", fake_close_db_pool)

    async with main.lifespan(FastAPI()):
        calls.append("inside")

    assert calls == [
        "logging:INFO:test",
        "initialize:postgresql://localhost/example",
        "inside",
        "close",
    ]


async def test_lifespan_skips_database_initialization_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    settings = build_settings(app_name="horizon", app_env="test")

    async def fake_initialize_db_pool(received_settings: Settings) -> None:
        calls.append("initialize")

    async def fake_close_db_pool() -> None:
        calls.append("close")

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(
        main,
        "configure_logging",
        lambda log_level, app_env: calls.append(f"logging:{log_level}:{app_env}"),
    )
    monkeypatch.setattr(main, "initialize_db_pool", fake_initialize_db_pool)
    monkeypatch.setattr(main, "close_db_pool", fake_close_db_pool)

    async with main.lifespan(FastAPI()):
        calls.append("inside")

    assert calls == [
        "logging:INFO:test",
        "inside",
        "close",
    ]