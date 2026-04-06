"""Unit tests for the health check API endpoints."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import health
from app.core.config import Settings
import main
from main import create_app


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


def test_health_endpoint_returns_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "is_db_configured", lambda: False)
    monkeypatch.setattr(health, "check_db_connection", lambda: False)
    monkeypatch.setattr(main, "configure_logging", lambda log_level, app_env: None)
    monkeypatch.setattr(main, "get_settings", lambda: build_settings())

    app = create_app()

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "not_configured"}


def test_health_endpoint_returns_degraded_status(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_check_db_connection() -> bool:
        return False

    monkeypatch.setattr(health, "is_db_configured", lambda: True)
    monkeypatch.setattr(health, "check_db_connection", fake_check_db_connection)
    monkeypatch.setattr(health, "get_last_connection_error", lambda: "timeout")
    monkeypatch.setattr(main, "configure_logging", lambda log_level, app_env: None)
    monkeypatch.setattr(main, "get_settings", lambda: build_settings())

    app = create_app()

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "database": "unavailable",
        "database_error": "timeout",
    }