"""Shared fixtures.

The whole Phase 1 suite runs with no Postgres, no Redis and no network, so CI
needs no services. Dependency failures are exercised by asserting the app
reports itself degraded rather than by standing real ones up.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("IP_HASH_SALT", "test-salt-value")
# Deliberately no LOG_JSON here: tests that assert the environment-derived
# default would otherwise be measuring this line instead of the code.


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Settings are cached; a test that changes the environment must not leak
    that into the next one."""
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings():
    from app.config import get_settings

    return get_settings()


@pytest.fixture
def app(settings):
    from app.main import create_app

    return create_app(settings)


@pytest.fixture
async def client(app) -> AsyncIterator:
    """ASGI-transport client: no socket is opened, and lifespan is not run, so
    the engine and Redis client stay uninitialised unless a test sets them up."""
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
