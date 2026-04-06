"""Application settings and environment configuration for the backend."""

from functools import lru_cache

from pydantic import Field, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "horizon-backend"
    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str | None = None
    database_pool_min_size: int = 1
    database_pool_max_size: int = 10
    database_command_timeout: float = 30.0
    clerk_secret_key: str | None = None
    clerk_webhook_signing_secret: str | None = None
    clerk_authorized_parties: list[str] = Field(default_factory=list)

    @field_validator("clerk_authorized_parties")
    @classmethod
    def _validate_clerk_authorized_parties(cls, value: list[str]) -> list[str]:
        cleaned_values = [item.strip() for item in value]
        if any(not item for item in cleaned_values):
            raise ValueError("CLERK_AUTHORIZED_PARTIES must contain non-empty origins.")

        return cleaned_values

    @model_validator(mode="after")
    def _validate_clerk_auth_configuration(self) -> "Settings":
        if self.clerk_secret_key and not self.clerk_authorized_parties:
            raise ValueError(
                "CLERK_AUTHORIZED_PARTIES must contain at least one origin when Clerk auth is enabled."
            )

        return self

    @computed_field
    @property
    def sqlalchemy_database_url(self) -> str | None:
        if not self.database_url:
            return None

        if self.database_url.startswith("postgresql+psycopg://"):
            return self.database_url

        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)

        if self.database_url.startswith("postgres://"):
            return self.database_url.replace("postgres://", "postgresql+psycopg://", 1)

        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
