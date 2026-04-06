"""Clerk authentication helpers and onboarding guards for protected routes."""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

import httpx
from fastapi import Depends, Request

from app.core.config import Settings, get_settings
from app.core.database import get_db_pool
from app.core.exceptions import (
    AuthConfigurationError,
    IncompleteOnboardingError,
    InvalidAuthTokenError,
    MissingAuthTokenError,
    MissingProfileError,
)
from app.core.logging import get_logger
from app.models.schemas import AuthenticatedUser, ProfileRecord

logger = get_logger(__name__)


@lru_cache
def _load_clerk_sdk() -> tuple[type[Any], type[Any]]:
    try:
        from clerk_backend_api import Clerk
        from clerk_backend_api.security.types import AuthenticateRequestOptions
    except ImportError as exc:
        raise AuthConfigurationError(
            "Clerk SDK is not installed. Add `clerk-backend-api` to the backend dependencies."
        ) from exc

    return Clerk, AuthenticateRequestOptions


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise MissingAuthTokenError()

    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise MissingAuthTokenError("Authorization header must use the format `Bearer <token>`.")

    return parts[1]


def _build_httpx_request(request: Request, bearer_token: str) -> httpx.Request:
    server = request.scope.get("server")
    scheme = str(request.scope.get("scheme") or "https")

    if isinstance(server, tuple) and len(server) == 2 and server[0]:
        host = str(server[0])
        port = server[1]
        default_port = 443 if scheme == "https" else 80
        netloc = host if port in (None, default_port) else f"{host}:{port}"
        base_url = httpx.URL(f"{scheme}://{netloc}")
    else:
        base_url = httpx.URL(f"{scheme}://localhost")

    url = base_url.copy_with(path=request.url.path, query=request.url.query.encode("utf-8"))

    return httpx.Request(
        method=request.method,
        url=url,
        headers={"authorization": f"Bearer {bearer_token}"},
    )


def _extract_subject(payload: Any) -> str | None:
    if payload is None:
        return None

    if isinstance(payload, dict):
        subject = payload.get("sub")
        return str(subject) if subject else None

    subject = getattr(payload, "sub", None)
    return str(subject) if subject else None


def _authenticate_request_state(request: Request, settings: Settings) -> Any:
    if not settings.clerk_secret_key:
        raise AuthConfigurationError("CLERK_SECRET_KEY must be configured for Clerk auth.")

    bearer_token = _extract_bearer_token(request.headers.get("authorization"))
    clerk_request = _build_httpx_request(request, bearer_token)
    Clerk, AuthenticateRequestOptions = _load_clerk_sdk()

    options = AuthenticateRequestOptions(authorized_parties=settings.clerk_authorized_parties or None)
    sdk = Clerk(bearer_auth=settings.clerk_secret_key)

    try:
        request_state = sdk.authenticate_request(clerk_request, options)
    except Exception as exc:
        logger.warning("auth.clerk_verification_failed", error=str(exc))
        raise InvalidAuthTokenError() from exc

    if not getattr(request_state, "is_signed_in", False):
        reason = getattr(request_state, "reason", None)
        logger.info("auth.token_rejected", reason=reason)
        raise InvalidAuthTokenError()

    return request_state


async def get_current_user(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    request_state = await asyncio.to_thread(_authenticate_request_state, request, settings)
    user_id = _extract_subject(getattr(request_state, "payload", None))

    if not user_id:
        logger.warning("auth.payload_missing_subject")
        raise InvalidAuthTokenError("Verified Clerk token payload was missing `sub`.")

    return AuthenticatedUser(user_id=user_id)


async def get_profile(auth_user: AuthenticatedUser = Depends(get_current_user)) -> ProfileRecord:
    pool = get_db_pool()
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT id, email, full_name, onboarding_complete
            FROM profiles
            WHERE id = $1
            """,
            auth_user.user_id,
        )

    if row is None:
        raise MissingProfileError()

    return ProfileRecord.model_validate(dict(row))


async def require_onboarding(profile: ProfileRecord = Depends(get_profile)) -> ProfileRecord:
    if not profile.onboarding_complete:
        raise IncompleteOnboardingError()

    return profile
