"""Per-IP rate limiting (constraint 6).

A fixed window in Redis, keyed by the *hashed* IP so the limiter stores no more
about a client than the database does.

**Fail-open on Redis errors, by design.** If Redis is unreachable the limiter
cannot do its job -- but the readiness probe already reports the instance
unhealthy for exactly the same reason, so the load balancer stops sending it
traffic. Failing closed would turn a Redis blip into a total outage while
adding no protection the drain does not already provide.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.core.security import client_ip, hash_ip

_log = get_logger("ratelimit")


@dataclass(frozen=True)
class RateLimitVerdict:
    allowed: bool
    remaining: int
    limit: int
    retry_after_seconds: int


async def _consume(
    redis: Redis, key: str, *, limit: int, window_seconds: int
) -> RateLimitVerdict:
    """Increment the window counter and set its expiry atomically.

    The pipeline matters: without it, a process that dies between INCR and
    EXPIRE leaves a key with no TTL, permanently locking that client out.
    """
    pipeline = redis.pipeline()
    pipeline.incr(key)
    pipeline.expire(key, window_seconds, nx=True)
    pipeline.ttl(key)
    count, _, ttl = await pipeline.execute()

    count = int(count)
    ttl = int(ttl)
    if ttl < 0:
        ttl = window_seconds

    return RateLimitVerdict(
        allowed=count <= limit,
        remaining=max(0, limit - count),
        limit=limit,
        retry_after_seconds=ttl,
    )


def _limit_headers(verdict: RateLimitVerdict) -> dict[str, str]:
    """Headers that must survive the error path, Retry-After above all: a
    client that cannot tell how long to wait will simply retry immediately."""
    return {
        "X-RateLimit-Limit": str(verdict.limit),
        "X-RateLimit-Remaining": str(verdict.remaining),
        "Retry-After": str(verdict.retry_after_seconds),
    }


async def enforce(
    request: Request,
    *,
    action: str,
    settings: Settings | None = None,
) -> RateLimitVerdict:
    """Apply the per-minute and per-day limits, raising RATE_LIMITED if either
    is exceeded."""
    settings = settings or get_settings()
    ip_hash = hash_ip(client_ip(request), settings.ip_hash_salt)

    if ip_hash is None:
        # No identifiable client (unlikely outside tests); nothing to limit on.
        limit = settings.rate_limit_per_minute
        return RateLimitVerdict(True, limit, limit, 0)

    try:
        redis = get_redis()
        minute = await _consume(
            redis,
            f"rl:{action}:m:{ip_hash}",
            limit=settings.rate_limit_per_minute,
            window_seconds=60,
        )
        if not minute.allowed:
            raise AppError(
                ErrorCode.RATE_LIMITED,
                detail=f"Limit is {minute.limit} requests per minute.",
                context={"window": "minute", "action": action},
                headers=_limit_headers(minute),
            )

        day = await _consume(
            redis,
            f"rl:{action}:d:{ip_hash}",
            limit=settings.rate_limit_per_day,
            window_seconds=86_400,
        )
        if not day.allowed:
            raise AppError(
                ErrorCode.RATE_LIMITED,
                detail=f"Limit is {day.limit} requests per day.",
                context={"window": "day", "action": action},
                headers=_limit_headers(day),
            )

        return minute

    except (RedisError, RuntimeError, OSError) as exc:
        # See the module docstring: readiness already drains this instance.
        _log.error("ratelimit.unavailable", error=type(exc).__name__, action=action)
        return RateLimitVerdict(
            True, settings.rate_limit_per_minute, settings.rate_limit_per_minute, 0
        )


def apply_headers(response, verdict: RateLimitVerdict) -> None:
    response.headers["X-RateLimit-Limit"] = str(verdict.limit)
    response.headers["X-RateLimit-Remaining"] = str(verdict.remaining)
    if not verdict.allowed:
        response.headers["Retry-After"] = str(verdict.retry_after_seconds)
