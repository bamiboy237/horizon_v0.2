"""Unit tests for backend configuration and settings helpers."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


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


def test_sqlalchemy_database_url_returns_none_when_database_url_is_missing() -> None:
    settings = build_settings()

    assert settings.sqlalchemy_database_url is None


@pytest.mark.parametrize(
    ("database_url", "expected"),
    [
        ("postgresql://localhost/example", "postgresql+psycopg://localhost/example"),
        ("postgres://localhost/example", "postgresql+psycopg://localhost/example"),
        ("postgresql+psycopg://localhost/example", "postgresql+psycopg://localhost/example"),
    ],
)
def test_sqlalchemy_database_url_normalizes_supported_formats(
    database_url: str,
    expected: str,
) -> None:
    settings = build_settings(database_url=database_url)

    assert settings.sqlalchemy_database_url == expected


def test_get_settings_returns_cached_instance() -> None:
    first = get_settings()
    second = get_settings()

    assert first is second


def test_clerk_settings_require_authorized_parties_when_secret_is_configured() -> None:
    with pytest.raises(ValidationError, match="CLERK_AUTHORIZED_PARTIES"):
        Settings(
            clerk_secret_key="sk_test_example",
            clerk_webhook_signing_secret="whsec_test_example",
            clerk_authorized_parties=[],
        )


def test_clerk_settings_trim_authorized_parties() -> None:
    settings = Settings(
        clerk_authorized_parties=[" http://localhost:3000 "],
    )

    assert settings.clerk_authorized_parties == ["http://localhost:3000"]