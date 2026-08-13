"""Shared Redis client.

Redis carries the job queue, the rate-limit counters and the single-use
download tokens. One pooled client per process.
"""

from __future__ import annotations

from redis.asyncio import Redis, from_url

from app.config import Settings

_client: Redis | None = None


def init_redis(settings: Settings) -> Redis:
    global _client
    if _client is None:
        _client = from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
            health_check_interval=30,
        )
    return _client


def get_redis() -> Redis:
    if _client is None:
        raise RuntimeError("Redis client not initialised; call init_redis() first")
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None
