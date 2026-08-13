from __future__ import annotations

import pytest


async def test_liveness_needs_no_dependencies(client):
    response = await client.get("/api/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_root_reports_service_identity(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "socialdownloader-api"


async def test_health_reports_degraded_when_dependencies_are_absent(client):
    """Nothing is initialised in this fixture, so both components must report
    down -- and the endpoint must still answer 200 with detail rather than
    blowing up."""
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["components"]["database"]["status"] == "down"
    assert body["components"]["redis"]["status"] == "down"


async def test_readiness_returns_503_when_degraded(client):
    response = await client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


async def test_health_reports_ok_when_dependencies_answer(client, monkeypatch):
    from app.api import health
    from app.models.schemas import ComponentHealth

    async def ok() -> ComponentHealth:
        return ComponentHealth(status="ok", latency_ms=0.1)

    monkeypatch.setattr(health, "_check_database", ok)
    monkeypatch.setattr(health, "_check_redis", ok)

    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    ready = await client.get("/api/health/ready")
    assert ready.status_code == 200


async def test_every_response_carries_a_request_id(client):
    response = await client.get("/api/health/live")
    assert response.headers["X-Request-ID"]


async def test_inbound_request_id_is_echoed(client):
    response = await client.get("/api/health/live", headers={"X-Request-ID": "trace-abc-123"})
    assert response.headers["X-Request-ID"] == "trace-abc-123"


async def test_absurdly_long_request_id_is_replaced(client):
    response = await client.get("/api/health/live", headers={"X-Request-ID": "x" * 5000})
    assert response.headers["X-Request-ID"] != "x" * 5000
    assert len(response.headers["X-Request-ID"]) == 32


@pytest.mark.parametrize(
    ("groq", "gemini"),
    [(None, None), ("key", None), (None, "key"), ("key", "key")],
)
async def test_features_reflect_configured_api_keys(monkeypatch, groq, gemini):
    import httpx

    from app.config import Settings
    from app.main import create_app

    settings = Settings(
        environment="test",
        ip_hash_salt="test-salt-value",
        groq_api_key=groq,
        gemini_api_key=gemini,
    )
    monkeypatch.setattr("app.api.health.get_settings", lambda: settings)

    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        body = (await ac.get("/api/features")).json()

    assert body["transcription"] is bool(groq)
    assert body["summarization"] is bool(gemini)
