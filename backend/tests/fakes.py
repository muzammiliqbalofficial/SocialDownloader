"""Small in-memory stand-ins so the offline suite can exercise Redis-backed
logic without a server."""

from __future__ import annotations

import time
from typing import Any


class FakeRedis:
    """Just enough of redis.asyncio for the rate limiter.

    Implements the INCR/EXPIRE(nx)/TTL pipeline it uses, with real expiry
    semantics so window rollover can be tested by moving `now` forward.
    """

    def __init__(self) -> None:
        self._values: dict[str, int] = {}
        self._expiry: dict[str, float] = {}
        self.now: float = time.time()

    # --- helpers for tests ---
    def advance(self, seconds: float) -> None:
        self.now += seconds

    def _sweep(self) -> None:
        expired = [k for k, at in self._expiry.items() if at <= self.now]
        for key in expired:
            self._values.pop(key, None)
            self._expiry.pop(key, None)

    # --- redis surface ---
    async def ping(self) -> bool:
        return True

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    async def incr(self, key: str) -> int:
        self._sweep()
        self._values[key] = self._values.get(key, 0) + 1
        return self._values[key]

    async def expire(self, key: str, seconds: int, nx: bool = False) -> bool:
        self._sweep()
        if nx and key in self._expiry:
            return False
        self._expiry[key] = self.now + seconds
        return True

    async def ttl(self, key: str) -> int:
        self._sweep()
        if key not in self._values:
            return -2
        if key not in self._expiry:
            return -1
        return max(0, int(self._expiry[key] - self.now))


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._queued: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def incr(self, key: str) -> FakePipeline:
        self._queued.append(("incr", (key,), {}))
        return self

    def expire(self, key: str, seconds: int, nx: bool = False) -> FakePipeline:
        self._queued.append(("expire", (key, seconds), {"nx": nx}))
        return self

    def ttl(self, key: str) -> FakePipeline:
        self._queued.append(("ttl", (key,), {}))
        return self

    async def execute(self) -> list[Any]:
        results = []
        for name, args, kwargs in self._queued:
            results.append(await getattr(self._redis, name)(*args, **kwargs))
        self._queued.clear()
        return results
