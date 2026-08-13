from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults_keep_the_ai_layer_off():
    settings = Settings(ip_hash_salt="test-salt-value")
    assert settings.transcription_enabled is False
    assert settings.summarization_enabled is False


def test_ai_flags_follow_the_keys():
    settings = Settings(ip_hash_salt="test-salt-value", groq_api_key="g", gemini_api_key="m")
    assert settings.transcription_enabled is True
    assert settings.summarization_enabled is True


@pytest.mark.parametrize("env", ["production", "staging"])
@pytest.mark.parametrize("salt", ["change-me", "dev", "secret", ""])
def test_weak_salt_is_rejected_outside_local(env, salt):
    with pytest.raises(ValidationError):
        Settings(environment=env, ip_hash_salt=salt)


def test_strong_salt_is_accepted_in_production():
    settings = Settings(environment="production", ip_hash_salt="9f3a-unique-random-value")
    assert settings.environment == "production"


def test_short_salt_is_rejected_everywhere():
    with pytest.raises(ValidationError):
        Settings(ip_hash_salt="short")


def test_invalid_log_level_is_rejected():
    with pytest.raises(ValidationError):
        Settings(ip_hash_salt="test-salt-value", log_level="CHATTY")


def test_log_level_is_normalised():
    assert Settings(ip_hash_salt="test-salt-value", log_level="debug").log_level == "DEBUG"


def test_cors_origins_parse_into_a_list():
    settings = Settings(
        ip_hash_salt="test-salt-value",
        cors_origins="http://localhost:3000, https://example.com ,",
    )
    assert settings.cors_origin_list == ["http://localhost:3000", "https://example.com"]


def test_json_logging_defaults_by_environment():
    assert Settings(ip_hash_salt="test-salt-value", environment="local").use_json_logs is False
    assert Settings(ip_hash_salt="test-salt-value", environment="production").use_json_logs is True
    explicit_off = Settings(
        ip_hash_salt="test-salt-value", environment="production", log_json=False
    )
    assert explicit_off.use_json_logs is False


def test_media_ttl_honours_the_fifteen_minute_ceiling():
    """Section 2.5 is a hard limit; the default must not exceed it."""
    assert Settings(ip_hash_salt="test-salt-value").media_ttl_seconds <= 900


def test_alembic_reuses_the_application_driver():
    settings = Settings(ip_hash_salt="test-salt-value")
    assert settings.alembic_database_url == settings.database_url
    assert "asyncpg" in settings.alembic_database_url
