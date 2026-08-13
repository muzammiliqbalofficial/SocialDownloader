"""Application configuration.

Everything comes from the environment. Nothing here has a production-safe
default that could be shipped by accident: `ip_hash_salt` is validated, and the
AI keys default to absent so the AI layer stays off unless deliberately enabled.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]

# Rejected outright rather than merely warned about: a shared default salt would
# make the stored ip_hash values trivially reversible via a rainbow table.
INSECURE_SALTS = {"", "change-me", "changeme", "secret", "salt", "dev"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core ---
    environment: Environment = "local"
    debug: bool = False
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    # Human-readable console logs locally; JSON everywhere else.
    log_json: bool | None = None

    # --- Infrastructure ---
    database_url: str = "postgresql+asyncpg://socialdl:socialdl@localhost:5432/socialdl"
    redis_url: str = "redis://localhost:6379/0"

    # --- Privacy ---
    # Salt for the SHA-256 of client IPs. Raw IPs are never stored (section 7).
    ip_hash_salt: str = Field(default="local-dev-only-salt", min_length=8)

    # --- Limits (enforced from Phase 3 onward, declared here so they are one
    # source of truth rather than magic numbers scattered across the worker) ---
    rate_limit_per_minute: int = 10
    rate_limit_per_day: int = 200
    max_batch_urls: int = 10
    # Downloaded media must not outlive this (section 2.5).
    media_ttl_seconds: int = 900
    download_token_ttl_seconds: int = 900
    job_timeout_seconds: int = 600
    max_filesize_mb: int = 2048
    # Above this the worker hands off to object storage instead of streaming.
    stream_threshold_mb: int = 200
    # Transcription input cap (section 5, Tier 3).
    max_transcription_seconds: int = 600

    # --- Optional AI layer. Absent key => capability hidden, endpoint 503. ---
    groq_api_key: str | None = None
    gemini_api_key: str | None = None

    # --- Storage ---
    gcs_bucket: str | None = None
    # Only used when no bucket is configured (local dev). Never assume anything
    # outside /tmp exists on Cloud Run.
    tmp_dir: str = "/tmp/socialdl"

    # --- CORS ---
    cors_origins: str = "http://localhost:3000"

    @field_validator("ip_hash_salt")
    @classmethod
    def _reject_weak_salt_in_prod(cls, value: str, info) -> str:
        env = (info.data or {}).get("environment")
        if env in ("staging", "production") and value.strip().lower() in INSECURE_SALTS:
            raise ValueError(
                "IP_HASH_SALT must be set to a unique random value outside local/test"
            )
        return value

    @field_validator("log_level")
    @classmethod
    def _normalise_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if level not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")
        return level

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def use_json_logs(self) -> bool:
        if self.log_json is not None:
            return self.log_json
        return self.environment != "local"

    @property
    def transcription_enabled(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def summarization_enabled(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def alembic_database_url(self) -> str:
        """Alembic drives the same asyncpg engine; no second sync driver."""
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
