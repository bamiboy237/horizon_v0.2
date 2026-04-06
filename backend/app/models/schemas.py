"""Pydantic schemas for authentication, profile, and opportunity payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AuthenticatedUser(BaseModel):
    user_id: str


class ProfileRecord(BaseModel):
    id: str
    email: str
    full_name: str | None = None
    onboarding_complete: bool = False


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str | None = None
    institution: str | None = None
    institution_type: str | None = None
    major: str | None = None
    cip_code: str | None = None
    gpa: float | None = None
    graduation_year: int | None = None
    citizenship: str | None = None
    state_residence: str | None = None
    first_generation: bool = False
    ethnicity: list[str] | None = None
    goals: list[str] | None = None
    interests: list[str] | None = None
    career_aspirations: list[str] | None = None
    onboarding_complete: bool = False
    embedding_model: str = "text-embedding-004"
    email_digest_enabled: bool = True
    email_digest_frequency: Literal["daily", "weekly", "monthly"] = "weekly"
    created_at: datetime
    updated_at: datetime


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, max_length=255)
    institution: str | None = Field(default=None, max_length=255)
    institution_type: str | None = Field(default=None, max_length=50)
    major: str | None = Field(default=None, max_length=255)
    cip_code: str | None = Field(default=None, max_length=10)
    gpa: float | None = Field(default=None, ge=0, le=4.0)
    graduation_year: int | None = Field(default=None, ge=1900, le=2100)
    citizenship: str | None = Field(default=None, max_length=100)
    state_residence: str | None = Field(default=None, max_length=50)
    first_generation: bool | None = None
    ethnicity: list[str] | None = None
    goals: list[str] | None = None
    interests: list[str] | None = None
    career_aspirations: list[str] | None = None
    email_digest_enabled: bool | None = None
    email_digest_frequency: Literal["daily", "weekly", "monthly"] | None = None
    onboarding_complete: bool | None = None

    @field_validator(
        "full_name",
        "institution",
        "institution_type",
        "major",
        "cip_code",
        "citizenship",
        "state_residence",
    )
    @classmethod
    def _validate_non_blank_strings(cls, value: str | None) -> str | None:
        if value is None:
            return value

        cleaned_value = value.strip()
        if not cleaned_value:
            raise ValueError("Field must be a non-empty string.")

        return cleaned_value

    @field_validator("ethnicity", "goals", "interests", "career_aspirations")
    @classmethod
    def _validate_non_blank_string_lists(
        cls,
        value: list[str] | None,
    ) -> list[str] | None:
        if value is None:
            return value

        cleaned_values = [item.strip() for item in value]
        if any(not item for item in cleaned_values):
            raise ValueError("List items must be non-empty strings.")

        return cleaned_values
