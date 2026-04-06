"""Unit tests for request and response schema validation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.schemas import ProfileResponse, ProfileUpdateRequest


def test_profile_update_request_accepts_allowed_fields() -> None:
    request = ProfileUpdateRequest(
        full_name="Ada Lovelace",
        institution="Bletchley Park",
        onboarding_complete=True,
    )

    assert request.model_dump(exclude_unset=True) == {
        "full_name": "Ada Lovelace",
        "institution": "Bletchley Park",
        "onboarding_complete": True,
    }


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("full_name", "   "),
        ("institution", "\t"),
        ("major", "\n"),
        ("citizenship", " "),
    ],
)
def test_profile_update_request_rejects_blank_scalar_strings(field_name: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ProfileUpdateRequest.model_validate({field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("goals", [""]),
        ("interests", [" "]),
        ("career_aspirations", ["", "scientist"]),
    ],
)
def test_profile_update_request_rejects_blank_list_items(field_name: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ProfileUpdateRequest.model_validate({field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("full_name", "x" * 256),
        ("institution", "x" * 256),
        ("cip_code", "1" * 11),
        ("gpa", 5.0),
        ("email_digest_frequency", "yearly"),
    ],
)
def test_profile_update_request_rejects_invalid_field_constraints(field_name: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ProfileUpdateRequest.model_validate({field_name: value})


@pytest.mark.parametrize(
    "field_name",
    [
        "id",
        "email",
        "profile_embedding",
        "interaction_embedding",
        "embedding_model",
        "created_at",
        "updated_at",
    ],
)
def test_profile_update_request_rejects_server_managed_fields(field_name: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ProfileUpdateRequest.model_validate({field_name: "not allowed"})

    error = exc_info.value.errors()[0]
    assert error["type"] == "extra_forbidden"
    assert error["loc"] == (field_name,)


def test_profile_response_accepts_full_row() -> None:
    profile = ProfileResponse.model_validate(
        {
            "id": "user_123",
            "email": "student@example.com",
            "full_name": "Ada Lovelace",
            "institution": "Bletchley Park",
            "institution_type": "University",
            "major": "Mathematics",
            "cip_code": "27.0101",
            "gpa": 3.95,
            "graduation_year": 2027,
            "citizenship": "US",
            "state_residence": "CA",
            "first_generation": True,
            "ethnicity": ["Women in STEM"],
            "goals": ["Scholarships"],
            "interests": ["math", "research"],
            "career_aspirations": ["scientist"],
            "onboarding_complete": True,
            "profile_embedding": [0.1, 0.2],
            "interaction_embedding": [0.3, 0.4],
            "embedding_model": "text-embedding-004",
            "email_digest_enabled": False,
            "email_digest_frequency": "weekly",
            "created_at": datetime(2026, 4, 5, tzinfo=UTC),
            "updated_at": datetime(2026, 4, 5, tzinfo=UTC),
        }
    )

    assert profile.id == "user_123"
    assert profile.email_digest_enabled is False