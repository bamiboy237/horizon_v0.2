"""Unit tests for backend exception handling and HTTP mapping."""

from __future__ import annotations

from fastapi import HTTPException

from app.core.exceptions import (
    AuthConfigurationError,
    HorizonError,
    HorizonHTTPError,
    IncompleteOnboardingError,
    InvalidAuthTokenError,
    InvalidWebhookPayloadError,
    InvalidWebhookSignatureError,
    MissingAuthTokenError,
    MissingProfileError,
)


def test_horizon_http_error_populates_fastapi_http_exception() -> None:
    error = HorizonHTTPError(418, "teapot")

    assert isinstance(error, HTTPException)
    assert isinstance(error, HorizonError)
    assert error.status_code == 418
    assert error.detail == "teapot"


def test_auth_configuration_error_uses_expected_defaults() -> None:
    error = AuthConfigurationError()

    assert error.status_code == 500
    assert error.detail == "Auth is not configured on the backend."


def test_other_http_errors_use_expected_status_codes() -> None:
    cases = [
        (MissingAuthTokenError(), 401, "Missing bearer token."),
        (InvalidAuthTokenError(), 401, "Invalid Clerk token."),
        (MissingProfileError(), 404, "Authenticated user profile was not found."),
        (
            IncompleteOnboardingError(),
            403,
            "Complete onboarding before accessing this resource.",
        ),
        (InvalidWebhookSignatureError(), 401, "Invalid Clerk webhook signature."),
        (InvalidWebhookPayloadError(), 400, "Invalid Clerk webhook payload."),
    ]

    for error, status_code, detail in cases:
        assert error.status_code == status_code
        assert error.detail == detail