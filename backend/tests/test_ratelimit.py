from __future__ import annotations

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import Settings
from app.core.errors import AppError, ErrorCode
from app.core.ratelimit import enforce
from tests.fakes import FakeRedis


def make_request(ip: str = "203.0.113.7"):
    from unittest.mock import Mock

    request = Mock()
    request.headers = {"x-forwarded-for": ip}
    request.client = Mock(host=ip)
    return request


@pytest.fixture
def settings():
    return Settings(
        ip_hash_salt="test-salt-value", rate_limit_per_minute=3, rate_limit_per_day=5
    )


@pytest.fixture
def fake_redis(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr("app.core.ratelimit.get_redis", lambda: redis)
    return redis


async def test_requests_under_the_limit_are_allowed(fake_redis, settings):
    for expected_remaining in (2, 1, 0):
        verdict = await enforce(make_request(), action="analyze", settings=settings)
        assert verdict.allowed
        assert verdict.remaining == expected_remaining


async def test_exceeding_the_minute_limit_raises(fake_redis, settings):
    for _ in range(3):
        await enforce(make_request(), action="analyze", settings=settings)

    with pytest.raises(AppError) as excinfo:
        await enforce(make_request(), action="analyze", settings=settings)

    assert excinfo.value.code is ErrorCode.RATE_LIMITED
    assert "per minute" in (excinfo.value.detail or "")


async def test_the_window_rolls_over(fake_redis, settings):
    for _ in range(3):
        await enforce(make_request(), action="analyze", settings=settings)

    fake_redis.advance(61)

    verdict = await enforce(make_request(), action="analyze", settings=settings)
    assert verdict.allowed


async def test_the_daily_limit_applies_across_windows(fake_redis, settings):
    # 5 per day, 3 per minute: spread across minutes to hit the daily cap.
    for _ in range(3):
        await enforce(make_request(), action="analyze", settings=settings)
    fake_redis.advance(61)
    for _ in range(2):
        await enforce(make_request(), action="analyze", settings=settings)
    fake_redis.advance(61)

    with pytest.raises(AppError) as excinfo:
        await enforce(make_request(), action="analyze", settings=settings)
    assert "per day" in (excinfo.value.detail or "")


async def test_clients_are_limited_independently(fake_redis, settings):
    for _ in range(3):
        await enforce(make_request("203.0.113.7"), action="analyze", settings=settings)

    verdict = await enforce(make_request("198.51.100.2"), action="analyze", settings=settings)
    assert verdict.allowed


async def test_actions_are_limited_independently(fake_redis, settings):
    for _ in range(3):
        await enforce(make_request(), action="analyze", settings=settings)

    verdict = await enforce(make_request(), action="download", settings=settings)
    assert verdict.allowed


async def test_the_key_contains_no_raw_ip(fake_redis, settings):
    await enforce(make_request("203.0.113.7"), action="analyze", settings=settings)
    assert all("203.0.113.7" not in key for key in fake_redis._values)


async def test_limiter_fails_open_when_redis_is_down(monkeypatch, settings):
    """Documented behaviour: readiness already drains an instance whose Redis
    is unreachable, so failing closed would add an outage without adding
    protection."""

    def boom():
        raise RedisConnectionError("no redis")

    monkeypatch.setattr("app.core.ratelimit.get_redis", boom)

    verdict = await enforce(make_request(), action="analyze", settings=settings)
    assert verdict.allowed is True


async def test_expiry_is_set_atomically_with_the_increment(fake_redis, settings):
    """A counter with no TTL would lock a client out permanently."""
    await enforce(make_request(), action="analyze", settings=settings)
    for key in fake_redis._values:
        assert await fake_redis.ttl(key) > 0
