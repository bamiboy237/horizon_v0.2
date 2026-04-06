"""Profile service helpers for loading, updating, and validating user data."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.database import get_db_pool
from app.core.exceptions import IncompleteProfileError

PROFILE_EDITABLE_COLUMNS: tuple[str, ...] = (
    "full_name",
    "institution",
    "institution_type",
    "major",
    "cip_code",
    "gpa",
    "graduation_year",
    "citizenship",
    "state_residence",
    "first_generation",
    "ethnicity",
    "goals",
    "interests",
    "career_aspirations",
    "email_digest_enabled",
    "email_digest_frequency",
    "onboarding_complete",
)

PROFILE_PRESERVE_NULL_COLUMNS: frozenset[str] = frozenset(
    {
        "first_generation",
        "email_digest_enabled",
        "email_digest_frequency",
        "onboarding_complete",
    }
)

PROFILE_ONBOARDING_REQUIRED_COLUMNS: tuple[str, ...] = (
    "full_name",
    "institution",
    "institution_type",
    "major",
    "gpa",
    "graduation_year",
    "citizenship",
    "state_residence",
    "goals",
    "interests",
)


def _has_value(value: Any) -> bool:
    if value is None:
        return False

    if isinstance(value, str):
        return bool(value.strip())

    if isinstance(value, list):
        return len(value) > 0 and all(isinstance(item, str) and bool(item.strip()) for item in value)

    return True


def _is_profile_ready_for_onboarding(profile: Mapping[str, Any]) -> bool:
    return all(_has_value(profile.get(column)) for column in PROFILE_ONBOARDING_REQUIRED_COLUMNS)


async def get_profile_by_user_id(user_id: str) -> dict[str, Any] | None:
    pool = get_db_pool()
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT *
            FROM profiles
            WHERE id = $1
            """,
            user_id,
        )

    if row is None:
        return None

    return dict(row)


async def update_profile_by_user_id(
    user_id: str,
    patch: Mapping[str, Any],
) -> dict[str, Any] | None:
    pool = get_db_pool()

    async with pool.acquire() as connection:
        async with connection.transaction():
            current_row = await connection.fetchrow(
                """
                SELECT *
                FROM profiles
                WHERE id = $1
                FOR UPDATE
                """,
                user_id,
            )

            if current_row is None:
                return None

            current_profile = dict(current_row)
            merged_profile = dict(current_profile)
            merged_profile.update(patch)

            if patch.get("onboarding_complete") is True and not _is_profile_ready_for_onboarding(merged_profile):
                raise IncompleteProfileError()

            if not _is_profile_ready_for_onboarding(merged_profile):
                patch = dict(patch)
                patch["onboarding_complete"] = False

            set_clauses: list[str] = []
            values: list[Any] = [user_id]

            for column in PROFILE_EDITABLE_COLUMNS:
                if column in patch:
                    value = patch[column]

                    if value is None and column in PROFILE_PRESERVE_NULL_COLUMNS:
                        continue

                    set_clauses.append(f"{column} = ${len(values) + 1}")
                    values.append(value)

            set_clauses.append("updated_at = NOW()")

            query = f"""
                UPDATE profiles
                SET {', '.join(set_clauses)}
                WHERE id = $1
                RETURNING *
            """

            row = await connection.fetchrow(query, *values)

    if row is None:
        return None

    return dict(row)