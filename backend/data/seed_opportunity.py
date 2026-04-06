"""Seed the Horizon opportunities table from the curated 150-record dataset."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.embeddings import get_embedding

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
SEED_FILE_NAME = "opportunities.json"
OPPORTUNITY_COLUMNS: tuple[str, ...] = (
    "source_url",
    "normalized_url",
    "title",
    "organization",
    "opportunity_type",
    "location",
    "funding_amount",
    "funding_type",
    "citizenship_required",
    "gpa_minimum",
    "major_requirements",
    "major_cip_requirements",
    "institution_types",
    "demographic_requirements",
    "eligibility_text",
    "deadline",
    "application_url",
    "required_materials",
    "estimated_prep_hours",
    "description",
    "embedding",
    "embedding_model",
    "last_verified",
)
OPPORTUNITY_UPDATE_COLUMNS: tuple[str, ...] = tuple(
    column for column in OPPORTUNITY_COLUMNS if column != "normalized_url"
)
EMBEDDING_SOURCE_FIELDS: tuple[str, ...] = (
    "title",
    "organization",
    "opportunity_type",
    "location",
    "eligibility_text",
    "required_materials",
    "description",
    "location",
    "funding_amount",
    "funding_type",
    "citizenship_required",
    "gpa_minimum",
    "major_requirements",
    "major_cip_requirements",
    "institution_types",
    "demographic_requirements",
    "eligibility_text",
    "deadline",
    "application_url",
    "required_materials",
    "estimated_prep_hours",
    "description",
)


class SeedSettings(BaseSettings):
    """Environment settings for the one-off seed script."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str | None = None
    database_pool_min_size: int = 1
    database_pool_max_size: int = 2
    database_command_timeout: float = 30.0


@dataclass(frozen=True)
class SeedStats:
    total: int
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped": self.skipped,
            "errors": self.errors,
        }


class SeedFileModel(BaseModel):
    """Backwards-compatible loader for the seed JSON file."""

    opportunities: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("opportunities")
    @classmethod
    def _validate_opportunities(
        cls, value: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        for record in value:
            if not isinstance(record, dict):
                raise ValueError("Each opportunity must be a JSON object.")

        return value


def load_settings() -> SeedSettings:
    """Load seed-specific settings from the environment."""

    return SeedSettings()


def load_opportunities(json_path: Path) -> list[dict[str, Any]]:
    """Load opportunities from either a raw list or wrapped JSON payload."""

    data = json.loads(json_path.read_text(encoding="utf-8"))

    if isinstance(data, list):
        return [dict(record) for record in data]

    if isinstance(data, dict):
        model = SeedFileModel.model_validate(data)
        return [dict(record) for record in model.opportunities]

    raise ValueError(
        "The opportunities seed file must contain a JSON array or an object with an opportunities key."
    )


def normalize_source_url(source_url: str) -> str:
    """Canonicalize source URLs for idempotent upserts."""

    stripped_url = source_url.strip()
    if not stripped_url:
        raise ValueError("source_url cannot be empty.")

    parts = urlsplit(stripped_url)
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"Invalid source_url: {source_url}")

    normalized_path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), normalized_path, parts.query, "")
    )


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    return text or None


def _clean_text_list(value: Any) -> list[str] | None:
    if value is None:
        return None

    if not isinstance(value, list):
        raise ValueError("Expected a list of strings.")

    cleaned_values = [str(item).strip() for item in value]
    if any(not item for item in cleaned_values):
        raise ValueError("List items must be non-empty strings.")

    return cleaned_values


def _clean_json_value(value: Any) -> str | None:
    if value is None:
        return None

    return json.dumps(value, ensure_ascii=False)


def _clean_numeric(value: Any) -> float | int | None:
    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError("Numeric values cannot be boolean.")

    return value


def _parse_deadline(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    if not isinstance(value, str):
        raise ValueError("deadline must be a datetime string.")

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def build_embedding_text(record: dict[str, Any]) -> str:
    """Build the canonical text used to generate the opportunity embedding."""

    parts: list[str] = []

    label_map = {
        "opportunity_type": "Opportunity Type",
        "eligibility_text": "Eligibility Text",
        "required_materials": "Required Materials",
    }

    for field in EMBEDDING_SOURCE_FIELDS:
        value = record.get(field)
        if value is None:
            continue

        if isinstance(value, list):
            normalized_value = ", ".join(
                str(item).strip() for item in value if str(item).strip()
            )
        else:
            normalized_value = str(value).strip()

        if normalized_value:
            label = label_map.get(field, field.replace("_", " ").title())
            parts.append(f"{label}: {normalized_value}")

    return "\n".join(parts)


def normalize_opportunity(
    record: dict[str, Any], *, verified_at: datetime | None = None
) -> dict[str, Any]:
    """Normalize a raw opportunity record into an insertable payload."""

    source_url = _clean_text(record.get("source_url"))
    title = _clean_text(record.get("title"))
    organization = _clean_text(record.get("organization"))
    opportunity_type = _clean_text(record.get("opportunity_type"))

    if source_url is None:
        raise ValueError("source_url is required.")
    if title is None:
        raise ValueError("title is required.")
    if organization is None:
        raise ValueError("organization is required.")
    if opportunity_type is None:
        raise ValueError("opportunity_type is required.")

    normalized_verified_at = verified_at or datetime.now(UTC)

    return {
        "source_url": source_url,
        "normalized_url": normalize_source_url(source_url),
        "title": title,
        "organization": organization,
        "opportunity_type": opportunity_type.lower(),
        "location": _clean_text(record.get("location")),
        "funding_amount": _clean_text(record.get("funding_amount")),
        "funding_type": _clean_text(record.get("funding_type")),
        "citizenship_required": _clean_text_list(record.get("citizenship_required")),
        "gpa_minimum": _clean_numeric(record.get("gpa_minimum")),
        "major_requirements": _clean_text_list(record.get("major_requirements")),
        "major_cip_requirements": _clean_text_list(
            record.get("major_cip_requirements")
        ),
        "institution_types": _clean_text_list(record.get("institution_types")),
        "demographic_requirements": _clean_json_value(
            copy.deepcopy(record.get("demographic_requirements"))
        ),
        "eligibility_text": _clean_text(record.get("eligibility_text")),
        "deadline": _parse_deadline(record.get("deadline")),
        "application_url": _clean_text(record.get("application_url")),
        "required_materials": _clean_text_list(record.get("required_materials")),
        "estimated_prep_hours": _clean_numeric(record.get("estimated_prep_hours")),
        "description": _clean_text(record.get("description")),
        "embedding_model": _clean_text(record.get("embedding_model"))
        or DEFAULT_EMBEDDING_MODEL,
        "last_verified": normalized_verified_at,
    }


def dedupe_opportunities(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Remove duplicate records after URL normalization while preserving order."""

    unique_records: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    skipped = 0

    for record in records:
        normalized_url = record["normalized_url"]
        if normalized_url in seen_urls:
            skipped += 1
            continue

        seen_urls.add(normalized_url)
        unique_records.append(record)

    return unique_records, skipped


def build_upsert_query() -> str:
    """Build the SQL statement used to insert or refresh a seed row."""

    columns = ", ".join(OPPORTUNITY_COLUMNS)
    placeholders = ", ".join(
        f"${index}" for index in range(1, len(OPPORTUNITY_COLUMNS) + 1)
    )
    assignments = ",\n                ".join(
        f"{column} = EXCLUDED.{column}" for column in OPPORTUNITY_UPDATE_COLUMNS
    )

    return f"""
        INSERT INTO opportunities ({columns})
        VALUES ({placeholders})
        ON CONFLICT (normalized_url) DO UPDATE
        SET {assignments}
    """


async def upsert_opportunity(connection: Any, record: dict[str, Any]) -> bool:
    """Insert or update a normalized opportunity row."""

    existing = await connection.fetchval(
        "SELECT 1 FROM opportunities WHERE normalized_url = $1",
        record["normalized_url"],
    )

    values = [record[column] for column in OPPORTUNITY_COLUMNS]
    await connection.execute(build_upsert_query(), *values)
    return existing is None


async def seed_opportunities(json_path: Path) -> dict[str, int]:
    """Seed the opportunities table from the curated dataset."""

    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required to seed opportunities.")

    raw_records = load_opportunities(json_path)
    validated_records: list[dict[str, Any]] = []
    errors = 0

    for record in raw_records:
        try:
            validated_records.append(normalize_opportunity(record))
        except (ValueError, ValidationError):
            errors += 1

    unique_records, skipped = dedupe_opportunities(validated_records)
    stats = SeedStats(total=len(raw_records), skipped=skipped, errors=errors)

    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=settings.database_pool_min_size,
        max_size=settings.database_pool_max_size,
        command_timeout=settings.database_command_timeout,
    )

    try:
        async with pool.acquire() as connection:
            inserted = 0
            updated = 0

            for record in unique_records:
                try:
                    record["embedding"] = get_embedding(build_embedding_text(record))
                    if await upsert_opportunity(connection, record):
                        inserted += 1
                    else:
                        updated += 1
                except Exception:
                    errors += 1

        stats = SeedStats(
            total=stats.total,
            inserted=inserted,
            updated=updated,
            skipped=stats.skipped,
            errors=errors,
        )
    finally:
        await pool.close()

    return stats.as_dict()


def main() -> None:
    """Run the seed process from the command line."""

    script_dir = Path(__file__).parent
    json_path = script_dir / SEED_FILE_NAME

    if not json_path.exists():
        raise SystemExit(f"Seed file not found: {json_path}")

    try:
        stats = asyncio.run(seed_opportunities(json_path))
    except Exception as exc:
        raise SystemExit(f"Fatal error while seeding opportunities: {exc}") from exc

    print("=" * 70)
    print("SEED SUMMARY")
    print("=" * 70)
    print(f"Total:    {stats['total']}")
    print(f"Inserted: {stats['inserted']}")
    print(f"Updated:  {stats['updated']}")
    print(f"Skipped:  {stats['skipped']}")
    print(f"Errors:   {stats['errors']}")

    if stats["errors"] > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
