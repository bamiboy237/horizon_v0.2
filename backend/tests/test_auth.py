"""Integration tests for the authentication and profile onboarding flow."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI

from app.api import auth
from app.core.config import Settings, get_settings
from app.core.exceptions import InvalidWebhookSignatureError
from app.core.security import get_current_user, get_profile, require_onboarding
from app.models.schemas import AuthenticatedUser, ProfileRecord


class FakeConnection:
    def __init__(self, profiles: dict[str, dict[str, object]]) -> None:
        self.profiles = profiles

    async def fetchrow(self, _: str, user_id: str) -> dict[str, object] | None:
        return self.profiles.get(user_id)

    async def execute(self, _: str, user_id: str, email: str, full_name: str | None) -> None:
        profile = dict(self.profiles.get(user_id, {}))
        profile.update(
            {
                "id": user_id,
                "email": email,
                "full_name": full_name,
            }
        )
        self.profiles[user_id] = profile


class FakeAcquire:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakePool:
    def __init__(self, profiles: dict[str, dict[str, object]]) -> None:
        self.connection = FakeConnection(profiles)

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.connection)


def make_settings() -> Settings:
    return Settings(
        clerk_secret_key="sk_test_example",
        clerk_webhook_signing_secret="whsec_test_example",
        clerk_authorized_parties=["http://localhost:3000"],
    )


@pytest_asyncio.fixture
async def auth_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()

    @app.get("/protected")
    async def protected(user: AuthenticatedUser = Depends(get_current_user)) -> dict[str, str]:
        return {"user_id": user.user_id}

    app.dependency_overrides[get_settings] = make_settings

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture
async def profile_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, dict[str, object]]]]:
    profiles: dict[str, dict[str, object]] = {}
    fake_pool = FakePool(profiles)
    monkeypatch.setattr("app.core.security.get_db_pool", lambda: fake_pool)

    app = FastAPI()

    @app.get("/profile")
    async def profile_route(profile: ProfileRecord = Depends(get_profile)) -> dict[str, object]:
        return profile.model_dump()

    @app.get("/onboarding")
    async def onboarding_route(profile: ProfileRecord = Depends(require_onboarding)) -> dict[str, object]:
        return profile.model_dump()

    app.dependency_overrides[get_settings] = make_settings

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, profiles


@pytest_asyncio.fixture
async def webhook_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, dict[str, object]]]]:
    profiles: dict[str, dict[str, object]] = {}
    fake_pool = FakePool(profiles)
    monkeypatch.setattr("app.api.auth.get_db_pool", lambda: fake_pool)

    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides[get_settings] = make_settings

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, profiles


def install_fake_clerk(monkeypatch: pytest.MonkeyPatch, *, is_signed_in: bool, subject: str | None = None) -> None:
    class FakeOptions:
        def __init__(self, authorized_parties=None) -> None:
            self.authorized_parties = authorized_parties

    class FakeClerk:
        def __init__(self, bearer_auth: str) -> None:
            self.bearer_auth = bearer_auth

        def authenticate_request(self, request: httpx.Request, options: FakeOptions) -> SimpleNamespace:
            if request.headers["authorization"] == "Bearer explode":
                raise RuntimeError("boom")
            return SimpleNamespace(
                is_signed_in=is_signed_in,
                payload={"sub": subject} if subject else None,
                reason=None if is_signed_in else "session-token-invalid",
                authorized_parties=options.authorized_parties,
            )

    monkeypatch.setattr("app.core.security._load_clerk_sdk", lambda: (FakeClerk, FakeOptions))


@pytest.mark.asyncio
async def test_missing_bearer_token_returns_401(auth_client: httpx.AsyncClient) -> None:
    response = await auth_client.get("/protected")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token."


@pytest.mark.asyncio
async def test_malformed_bearer_header_returns_401(
    auth_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_clerk(monkeypatch, is_signed_in=True, subject="user_123")

    response = await auth_client.get("/protected", headers={"Authorization": "Token abc"})

    assert response.status_code == 401
    assert "Bearer <token>" in response.json()["detail"]


@pytest.mark.asyncio
async def test_invalid_clerk_token_returns_401(
    auth_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_clerk(monkeypatch, is_signed_in=False)

    response = await auth_client.get("/protected", headers={"Authorization": "Bearer invalid"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid Clerk token."


@pytest.mark.asyncio
async def test_valid_clerk_token_returns_user_id(
    auth_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_clerk(monkeypatch, is_signed_in=True, subject="user_abc")

    response = await auth_client.get("/protected", headers={"Authorization": "Bearer valid"})

    assert response.status_code == 200
    assert response.json() == {"user_id": "user_abc"}


@pytest.mark.asyncio
async def test_missing_local_profile_returns_404(
    profile_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = profile_client
    install_fake_clerk(monkeypatch, is_signed_in=True, subject="user_missing")

    response = await client.get("/profile", headers={"Authorization": "Bearer valid"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Authenticated user profile was not found."


@pytest.mark.asyncio
async def test_onboarding_gate_returns_403_when_incomplete(
    profile_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, profiles = profile_client
    profiles["user_incomplete"] = {
        "id": "user_incomplete",
        "email": "student@example.com",
        "full_name": "Student Example",
        "onboarding_complete": False,
    }
    install_fake_clerk(monkeypatch, is_signed_in=True, subject="user_incomplete")

    response = await client.get("/onboarding", headers={"Authorization": "Bearer valid"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Complete onboarding before accessing this resource."


@pytest.mark.asyncio
async def test_onboarding_gate_passes_when_complete(
    profile_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, profiles = profile_client
    profiles["user_complete"] = {
        "id": "user_complete",
        "email": "student@example.com",
        "full_name": "Student Example",
        "onboarding_complete": True,
    }
    install_fake_clerk(monkeypatch, is_signed_in=True, subject="user_complete")

    response = await client.get("/onboarding", headers={"Authorization": "Bearer valid"})

    assert response.status_code == 200
    assert response.json()["id"] == "user_complete"


def make_clerk_event(event_type: str, *, email: str | None = "student@example.com") -> dict[str, object]:
    email_addresses = []
    primary_email_address_id = None

    if email is not None:
        primary_email_address_id = "email_123"
        email_addresses.append(
            {
                "id": primary_email_address_id,
                "email_address": email,
            }
        )

    return {
        "type": event_type,
        "data": {
            "id": "user_123",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "primary_email_address_id": primary_email_address_id,
            "email_addresses": email_addresses,
        },
    }


@pytest.mark.asyncio
async def test_invalid_svix_signature_is_rejected(
    webhook_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = webhook_client
    monkeypatch.setattr(
        "app.api.auth._verify_clerk_webhook",
        lambda body, headers, settings: (_ for _ in ()).throw(InvalidWebhookSignatureError()),
    )

    response = await client.post("/webhooks/clerk", content=b"{}")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid Clerk webhook signature."


@pytest.mark.asyncio
async def test_webhook_verifier_receives_raw_bytes(
    webhook_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = webhook_client
    captured: dict[str, object] = {}

    class FakeWebhook:
        def __init__(self, signing_secret: str) -> None:
            captured["signing_secret"] = signing_secret

        def verify(self, body: bytes, headers: dict[str, str]) -> dict[str, object]:
            captured["body"] = body
            captured["headers"] = dict(headers)
            return make_clerk_event("user.created")

    monkeypatch.setattr("app.api.auth._load_svix_webhook", lambda: FakeWebhook)

    response = await client.post("/webhooks/clerk", content=b"\xff\xfe")

    assert response.status_code == 200
    assert captured["body"] == b"\xff\xfe"


@pytest.mark.asyncio
async def test_webhook_rejects_non_object_payload(
    webhook_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = webhook_client

    class FakeWebhook:
        def __init__(self, signing_secret: str) -> None:
            self.signing_secret = signing_secret

        def verify(self, body: bytes, headers: dict[str, str]) -> list[object]:
            return []

    monkeypatch.setattr("app.api.auth._load_svix_webhook", lambda: FakeWebhook)

    response = await client.post("/webhooks/clerk", content=b"{}")

    assert response.status_code == 400
    assert response.json()["detail"] == "Clerk webhook payload must be a JSON object."


@pytest.mark.asyncio
async def test_user_created_inserts_profile(
    webhook_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, profiles = webhook_client
    monkeypatch.setattr(
        "app.api.auth._verify_clerk_webhook",
        lambda body, headers, settings: make_clerk_event("user.created"),
    )

    response = await client.post("/webhooks/clerk", content=b"{}")

    assert response.status_code == 200
    assert profiles["user_123"]["email"] == "student@example.com"
    assert profiles["user_123"]["full_name"] == "Ada Lovelace"


@pytest.mark.asyncio
async def test_repeated_user_created_does_not_create_duplicates(
    webhook_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, profiles = webhook_client
    monkeypatch.setattr(
        "app.api.auth._verify_clerk_webhook",
        lambda body, headers, settings: make_clerk_event("user.created"),
    )

    first_response = await client.post("/webhooks/clerk", content=b"{}")
    second_response = await client.post("/webhooks/clerk", content=b"{}")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert len(profiles) == 1
    assert profiles["user_123"]["email"] == "student@example.com"


@pytest.mark.asyncio
async def test_user_updated_updates_existing_profile(
    webhook_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, profiles = webhook_client
    profiles["user_123"] = {
        "id": "user_123",
        "email": "old@example.com",
        "full_name": "Old Name",
        "onboarding_complete": False,
    }
    monkeypatch.setattr(
        "app.api.auth._verify_clerk_webhook",
        lambda body, headers, settings: make_clerk_event("user.updated", email="new@example.com"),
    )

    response = await client.post("/webhooks/clerk", content=b"{}")

    assert response.status_code == 200
    assert profiles["user_123"]["email"] == "new@example.com"
    assert profiles["user_123"]["full_name"] == "Ada Lovelace"


@pytest.mark.asyncio
async def test_user_updated_preserves_local_profile_fields(
    webhook_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, profiles = webhook_client
    profiles["user_123"] = {
        "id": "user_123",
        "email": "old@example.com",
        "full_name": "Old Name",
        "onboarding_complete": False,
        "institution": "MIT",
        "email_digest_enabled": False,
    }
    monkeypatch.setattr(
        "app.api.auth._verify_clerk_webhook",
        lambda body, headers, settings: make_clerk_event("user.updated", email="new@example.com"),
    )

    response = await client.post("/webhooks/clerk", content=b"{}")

    assert response.status_code == 200
    assert profiles["user_123"]["email"] == "new@example.com"
    assert profiles["user_123"]["full_name"] == "Ada Lovelace"
    assert profiles["user_123"]["institution"] == "MIT"
    assert profiles["user_123"]["email_digest_enabled"] is False


@pytest.mark.asyncio
async def test_unsupported_event_types_are_ignored(
    webhook_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, profiles = webhook_client
    monkeypatch.setattr(
        "app.api.auth._verify_clerk_webhook",
        lambda body, headers, settings: make_clerk_event("session.ended"),
    )

    response = await client.post("/webhooks/clerk", content=b"{}")

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert profiles == {}


@pytest.mark.asyncio
async def test_user_created_without_email_fails_clearly(
    webhook_client: tuple[httpx.AsyncClient, dict[str, dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = webhook_client
    monkeypatch.setattr(
        "app.api.auth._verify_clerk_webhook",
        lambda body, headers, settings: make_clerk_event("user.created", email=None),
    )

    response = await client.post("/webhooks/clerk", content=b"{}")

    assert response.status_code == 400
    assert response.json()["detail"] == "Clerk webhook user payload is missing a primary email."
