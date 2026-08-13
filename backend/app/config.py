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
    # How long a stored object is retained after its token spends (D-016).
    # Coverage is fed by bytes written to the ASGI send channel, and those are
    # not bytes the client received -- uvicorn's socket buffer and Cloud Run's
    # frontend proxy both accept bytes in flight, so a client dropping near the
    # end can produce complete coverage while missing the tail. This window is
    # what absorbs that overcounting. It can shorten an object's life but never
    # extend it past the TTL above.
    download_grace_seconds: int = 180
    job_timeout_seconds: int = 600
    max_filesize_mb: int = 2048
    # Above this the download endpoint hands out a signed URL instead of
    # proxy-streaming. Decided on the *actual* size measured after the job, not
    # the analyze-time estimate, so being wrong costs latency and never
    # success (D-001).
    stream_threshold_mb: int = 200
    # Deliberately short (D-015): a signed URL is a bearer token, and for its
    # lifetime anyone holding it can fetch the object. Long enough to start a
    # download on a slow connection, short enough that a shared link is stale
    # almost immediately.
    signed_url_ttl_seconds: int = 120
    # Per-IP daily budget for signed-URL issuance, charged at issuance for the
    # object's full size so a client cannot mint cheap URLs and fan the egress
    # out elsewhere. Bytes rather than request counts: ten 2 GB URLs and ten
    # 2 MB URLs are identical under a count limit and three orders of magnitude
    # apart on the bill.
    signed_url_daily_byte_budget: int = 20 * 1024**3
    # Retries permitted per download token before it is refused. The backstop
    # against unbounded egress, since coverage tracking deliberately allows a
    # dropped transfer to be resumed for the whole TTL (D-013).
    max_download_attempts: int = 10
    # Transcription input cap (section 5, Tier 3).
    max_transcription_seconds: int = 600

    # --- Platform toggles ---
    # Snapchat ships disabled (decision D-004): Spotlight-only, minimal
    # metadata, unreliable Stories. When off it is omitted from the capability
    # registry entirely rather than shown as a failing tab.
    snapchat_enabled: bool = False

    # --- Extraction ---
    # Hard ceiling on a single yt-dlp invocation. Analyze should take 2-5s;
    # anything near this limit means the platform is not answering.
    extractor_timeout_seconds: int = 45

    # Concurrent yt-dlp subprocesses per instance. This is a *memory* bound,
    # not a throughput knob: each subprocess costs ~45 MiB at minimum (measured
    # floor) and more on a large format list, so an unbounded count OOMs the
    # container long before it saturates CPU. Keep container concurrency in
    # proportion -- see deploy/cloudrun/README.md.
    max_concurrent_extractions: int = 4
    # How long a request waits for a free slot before giving up. A fast honest
    # rejection beats a request that dies at the load balancer's timeout.
    extraction_queue_wait_seconds: float = 2.0
    # Retry-After sent when the extractor is saturated. Roughly one typical
    # extraction, so a client that obeys it arrives when a slot has freed.
    extraction_busy_retry_after_seconds: int = 5

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
