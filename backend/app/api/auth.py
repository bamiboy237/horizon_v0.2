"""Clerk webhook endpoints for provisioning and synchronizing user profiles."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.core.config import Settings, get_settings
from app.core.database import get_db_pool
from app.core.exceptions import (
    AuthConfigurationError,
    InvalidWebhookPayloadError,
    InvalidWebhookSignatureError,
)
from app.core.logging import get_logger

router = APIRouter(prefix="/webhooks", tags=["auth"])
logger = get_logger(__name__)


@lru_cache
def _load_svix_webhook() -> type[Any]:
    try:
        from svix.webhooks import Webhook
    except ImportError as exc:
        raise AuthConfigurationError(
            "Svix is not installed. Add `svix` to the backend dependencies."
        ) from exc

    return Webhook


def _get_primary_email(user_data: dict[str, Any]) -> str | None:
    primary_id = user_data.get("primary_email_address_id")
    email_addresses = user_data.get("email_addresses") or []

    for email_address in email_addresses:
        if email_address.get("id") == primary_id and email_address.get("email_address"):
            return str(email_address["email_address"])

    for email_address in email_addresses:
        if email_address.get("email_address"):
            return str(email_address["email_address"])

    return None


def _get_full_name(user_data: dict[str, Any]) -> str | None:
    first_name = (user_data.get("first_name") or "").strip()
    last_name = (user_data.get("last_name") or "").strip()
    full_name = f"{first_name} {last_name}".strip()
    return full_name or None


def _verify_clerk_webhook(
    body: bytes,
    headers: dict[str, str],
    settings: Settings,
) -> dict[str, Any]:
    if not settings.clerk_webhook_signing_secret:
        raise AuthConfigurationError(
            "CLERK_WEBHOOK_SIGNING_SECRET must be configured for Clerk webhook verification."
        )

    Webhook = _load_svix_webhook()
    verifier = Webhook(settings.clerk_webhook_signing_secret)

    try:
        payload = verifier.verify(body, headers)
    except Exception as exc:
        logger.info("auth.webhook_signature_invalid")
        raise InvalidWebhookSignatureError() from exc

    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidWebhookPayloadError("Clerk webhook payload was not valid UTF-8.") from exc

    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise InvalidWebhookPayloadError("Clerk webhook payload was not valid JSON.") from exc

    return payload


async def _upsert_profile(
    *,
    user_id: str,
    email: str,
    full_name: str | None,
) -> None:
    pool = get_db_pool()
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO profiles (id, email, full_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (id) DO UPDATE
            SET email = EXCLUDED.email,
                full_name = EXCLUDED.full_name,
                updated_at = NOW()
            """,
            user_id,
            email,
            full_name,
        )


async def _handle_user_created(user_data: dict[str, Any]) -> dict[str, str]:
    user_id = user_data.get("id")
    email = _get_primary_email(user_data)

    if not user_id:
        raise InvalidWebhookPayloadError("Clerk webhook user payload is missing `id`.")
    if not email:
        raise InvalidWebhookPayloadError("Clerk webhook user payload is missing a primary email.")

    await _upsert_profile(
        user_id=str(user_id),
        email=email,
        full_name=_get_full_name(user_data),
    )

    logger.info("auth.webhook_profile_upserted", event_type="user.created", user_id=user_id)
    return {"status": "ok", "event_type": "user.created", "user_id": str(user_id)}


async def _handle_user_updated(user_data: dict[str, Any]) -> dict[str, str]:
    user_id = user_data.get("id")
    email = _get_primary_email(user_data)

    if not user_id:
        raise InvalidWebhookPayloadError("Clerk webhook user payload is missing `id`.")
    if not email:
        raise InvalidWebhookPayloadError("Clerk webhook user payload is missing a primary email.")

    await _upsert_profile(
        user_id=str(user_id),
        email=email,
        full_name=_get_full_name(user_data),
    )

    logger.info("auth.webhook_profile_upserted", event_type="user.updated", user_id=user_id)
    return {"status": "ok", "event_type": "user.updated", "user_id": str(user_id)}


@router.post("/clerk")
async def handle_clerk_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    body = await request.body()
    headers = {
        "svix-id": request.headers.get("svix-id", ""),
        "svix-timestamp": request.headers.get("svix-timestamp", ""),
        "svix-signature": request.headers.get("svix-signature", ""),
    }

    event = _verify_clerk_webhook(body, headers, settings)
    if not isinstance(event, dict):
        raise InvalidWebhookPayloadError("Clerk webhook payload must be a JSON object.")

    event_type = event.get("type")
    user_data = event.get("data")

    if not isinstance(event_type, str):
        raise InvalidWebhookPayloadError("Clerk webhook payload is missing `type`.")

    if event_type not in {"user.created", "user.updated"}:
        logger.info("auth.webhook_ignored", event_type=event_type)
        return {"status": "ignored", "event_type": str(event_type)}

    if not isinstance(user_data, dict):
        raise InvalidWebhookPayloadError("Clerk webhook payload is missing `data`.")

    logger.info("auth.webhook_received", event_type=event_type, user_id=user_data.get("id"))

    if event_type == "user.created":
        return await _handle_user_created(user_data)
    return await _handle_user_updated(user_data)
