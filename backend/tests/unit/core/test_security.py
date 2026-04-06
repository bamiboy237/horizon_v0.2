"""Unit tests for Clerk auth helpers and onboarding guards."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import httpx
from starlette.requests import Request

from app.core import security
from app.core.config import Settings
from app.core.exceptions import (
    AuthConfigurationError,
    IncompleteOnboardingError,
    InvalidAuthTokenError,
    MissingAuthTokenError,
    MissingProfileError,
)
from app.models.schemas import AuthenticatedUser, ProfileRecord


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
    def __init__(self, row: dict[str, object] | None) -> None:
        self.row = row
        self.fetchrow_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, query: str, user_id: str) -> dict[str, object] | None:
        self.fetchrow_calls.append((query, (user_id,)))
        return self.row


class FakeAcquireContext:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakePool:
    def __init__(self, row: dict[str, object] | None) -> None:
        self.connection = FakeConnection(row)

    def acquire(self) -> FakeAcquireContext:
        return FakeAcquireContext(self.connection)


def build_request(authorization: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": ("api.example.com", 443),
            "path": "/api/profile",
            "headers": [(b"authorization", authorization.encode("utf-8"))],
        }
    )


def test_load_clerk_sdk_imports_expected_classes(monkeypatch: pytest.MonkeyPatch) -> None:
    security._load_clerk_sdk.cache_clear()

    clerk_module = ModuleType("clerk_backend_api")
    setattr(clerk_module, "__path__", [])

    security_module = ModuleType("clerk_backend_api.security")
    setattr(security_module, "__path__", [])

    types_module = ModuleType("clerk_backend_api.security.types")

    class Clerk:
        def __init__(self, bearer_auth: str) -> None:
            self.bearer_auth = bearer_auth

    class AuthenticateRequestOptions:
        def __init__(self, authorized_parties: list[str] | None = None) -> None:
            self.authorized_parties = authorized_parties

    setattr(clerk_module, "Clerk", Clerk)
    setattr(types_module, "AuthenticateRequestOptions", AuthenticateRequestOptions)
    setattr(security_module, "types", types_module)

    monkeypatch.setitem(sys.modules, "clerk_backend_api", clerk_module)
    monkeypatch.setitem(sys.modules, "clerk_backend_api.security", security_module)
    monkeypatch.setitem(sys.modules, "clerk_backend_api.security.types", types_module)

    loaded_clerk, loaded_options = security._load_clerk_sdk()

    assert loaded_clerk is Clerk
    assert loaded_options is AuthenticateRequestOptions


@pytest.mark.parametrize(
    ("authorization", "expected_message"),
    [
        (None, "Missing bearer token."),
        ("", "Missing bearer token."),
        ("Token abc123", "Authorization header must use the format `Bearer <token>`."),
        ("Bearer ", "Authorization header must use the format `Bearer <token>`."),
    ],
)
def test_extract_bearer_token_rejects_invalid_values(
    authorization: str | None,
    expected_message: str,
) -> None:
    with pytest.raises(MissingAuthTokenError, match=expected_message):
        security._extract_bearer_token(authorization)


def test_extract_bearer_token_accepts_valid_header() -> None:
    assert security._extract_bearer_token("  Bearer   token-123  ") == "token-123"


def test_build_httpx_request_forwards_browser_headers() -> None:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("api.example.com", 443),
            "path": "/api/research",
            "query_string": b"step=1",
            "headers": [],
        }
    )

    built_request = security._build_httpx_request(request, "token-123")

    assert built_request.headers["authorization"] == "Bearer token-123"
    assert str(built_request.url) == "https://api.example.com/api/research?step=1"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"sub": "user_123"}, "user_123"),
        (SimpleNamespace(sub="user_456"), "user_456"),
        ({"sub": 42}, "42"),
        (None, None),
        ({}, None),
        (SimpleNamespace(sub=None), None),
    ],
)
def test_extract_subject_handles_mixed_payload_shapes(
    payload: object,
    expected: str | None,
) -> None:
    assert security._extract_subject(payload) == expected


def test_authenticate_request_state_returns_signed_in_request_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeRequestState:
        is_signed_in = True
        reason = None
        payload = {"sub": "user_123"}

    class FakeAuthenticateRequestOptions:
        def __init__(self, authorized_parties: list[str] | None = None) -> None:
            self.authorized_parties = authorized_parties

    class FakeClerk:
        def __init__(self, bearer_auth: str) -> None:
            captured["bearer_auth"] = bearer_auth

        def authenticate_request(self, clerk_request: object, options: object) -> FakeRequestState:
            captured["request"] = clerk_request
            captured["options"] = options
            return FakeRequestState()

    monkeypatch.setattr(security, "_load_clerk_sdk", lambda: (FakeClerk, FakeAuthenticateRequestOptions))

    request_state = security._authenticate_request_state(
        build_request("Bearer token-123"),
        build_settings(clerk_secret_key="secret", clerk_authorized_parties=["https://app.example"]),
    )

    assert isinstance(request_state, FakeRequestState)
    assert captured["bearer_auth"] == "secret"
    request = captured["request"]
    options = captured["options"]

    assert isinstance(request, httpx.Request)
    assert request.headers["authorization"] == "Bearer token-123"
    assert options.authorized_parties == ["https://app.example"]


def test_authenticate_request_state_rejects_unsigned_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRequestState:
        is_signed_in = False
        reason = "not signed in"
        payload = {"sub": "user_123"}

    class FakeAuthenticateRequestOptions:
        def __init__(self, authorized_parties: list[str] | None = None) -> None:
            self.authorized_parties = authorized_parties

    class FakeClerk:
        def __init__(self, bearer_auth: str) -> None:
            self.bearer_auth = bearer_auth

        def authenticate_request(self, clerk_request: object, options: object) -> FakeRequestState:
            return FakeRequestState()

    monkeypatch.setattr(security, "_load_clerk_sdk", lambda: (FakeClerk, FakeAuthenticateRequestOptions))

    with pytest.raises(InvalidAuthTokenError):
        security._authenticate_request_state(
            build_request("Bearer token-123"),
            build_settings(clerk_secret_key="secret"),
        )


def test_authenticate_request_state_requires_clerk_secret_key() -> None:
    with pytest.raises(AuthConfigurationError, match="CLERK_SECRET_KEY must be configured"):
        security._authenticate_request_state(build_request("Bearer token-123"), build_settings())


async def test_get_current_user_returns_authenticated_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        security,
        "_authenticate_request_state",
        lambda request, settings: SimpleNamespace(payload={"sub": "user_123"}),
    )

    user = await security.get_current_user(build_request("Bearer token-123"), build_settings())

    assert user == AuthenticatedUser(user_id="user_123")


async def test_get_current_user_rejects_missing_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        security,
        "_authenticate_request_state",
        lambda request, settings: SimpleNamespace(payload={}),
    )

    with pytest.raises(InvalidAuthTokenError, match="missing `sub`"):
        await security.get_current_user(build_request("Bearer token-123"), build_settings())


async def test_get_profile_returns_pydantic_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pool = FakePool(
        {
            "id": "user_123",
            "email": "student@example.com",
            "full_name": "Student Example",
            "onboarding_complete": True,
        }
    )
    monkeypatch.setattr(security, "get_db_pool", lambda: fake_pool)

    profile = await security.get_profile(AuthenticatedUser(user_id="user_123"))

    assert profile == ProfileRecord(
        id="user_123",
        email="student@example.com",
        full_name="Student Example",
        onboarding_complete=True,
    )
    query, params = fake_pool.connection.fetchrow_calls[0]
    assert "FROM profiles" in query
    assert params == ("user_123",)


async def test_get_profile_raises_when_profile_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pool = FakePool(None)
    monkeypatch.setattr(security, "get_db_pool", lambda: fake_pool)

    with pytest.raises(MissingProfileError):
        await security.get_profile(AuthenticatedUser(user_id="user_123"))


async def test_require_onboarding_returns_complete_profile() -> None:
    profile = ProfileRecord(
        id="user_123",
        email="student@example.com",
        onboarding_complete=True,
    )

    assert await security.require_onboarding(profile) is profile


async def test_require_onboarding_rejects_incomplete_profile() -> None:
    profile = ProfileRecord(
        id="user_123",
        email="student@example.com",
        onboarding_complete=False,
    )

    with pytest.raises(IncompleteOnboardingError):
        await security.require_onboarding(profile)