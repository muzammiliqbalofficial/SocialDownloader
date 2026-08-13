"""Health, readiness and liveness.

Three endpoints because they answer three different questions:
  /live   -- is the process up? (no dependencies touched; Cloud Run liveness)
  /ready  -- can it serve traffic? (503 when a dependency is down)
  /health -- human/dashboard view with per-component detail
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Response, status

from app.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import ComponentHealth, FeatureFlags, HealthResponse

router = APIRouter(tags=["health"])
_log = get_logger("health")

# A health probe must fail fast; a hanging dependency should surface as "down",
# not as a probe that times out at the load balancer.
PROBE_TIMEOUT_SECONDS = 2.0


async def _check_database() -> ComponentHealth:
    from sqlalchemy import text

    from app.models.db import get_engine

    started = time.perf_counter()
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        return ComponentHealth(status="down", detail=type(exc).__name__)
    return ComponentHealth(
        status="ok", latency_ms=round((time.perf_counter() - started) * 1000, 2)
    )


async def _check_redis() -> ComponentHealth:
    from app.core.redis import get_redis

    started = time.perf_counter()
    try:
        await get_redis().ping()
    except Exception as exc:
        return ComponentHealth(status="down", detail=type(exc).__name__)
    return ComponentHealth(
        status="ok", latency_ms=round((time.perf_counter() - started) * 1000, 2)
    )


async def _collect() -> dict[str, ComponentHealth]:
    return {"database": await _check_database(), "redis": await _check_redis()}


@router.get("/health/live", summary="Liveness probe")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health", response_model=HealthResponse, summary="Component health")
async def health() -> HealthResponse:
    settings = get_settings()
    components = await _collect()
    degraded = any(c.status != "ok" for c in components.values())
    return HealthResponse(
        status="degraded" if degraded else "ok",
        version=settings.app_version,
        environment=settings.environment,
        components=components,
    )


@router.get("/health/ready", response_model=HealthResponse, summary="Readiness probe")
async def ready(response: Response) -> HealthResponse:
    result = await health()
    if result.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        _log.warning(
            "health.not_ready",
            components={k: v.status for k, v in result.components.items()},
        )
    return result


@router.get("/features", response_model=FeatureFlags, summary="Enabled optional features")
async def features() -> FeatureFlags:
    """The frontend renders from this: a feature with no API key is hidden, not
    shown-then-broken."""
    settings = get_settings()
    return FeatureFlags(
        transcription=settings.transcription_enabled,
        summarization=settings.summarization_enabled,
        batch=True,
    )
