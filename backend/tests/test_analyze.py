"""POST /api/analyze, with the extractor stubbed from committed fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.errors import AppError, ErrorCode
from app.extractors.ytdlp_adapter import normalise

FIXTURES = Path(__file__).parent / "fixtures"
YOUTUBE_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def stub_extractor(monkeypatch):
    """Replace the adapter's network call, keeping the real normalisation and
    the real validation in the route."""

    def _install(info: dict | None = None, *, error: AppError | None = None):
        from app.extractors import ytdlp_adapter

        async def fake_fetch(url: str, *, timeout: int) -> dict:
            if error is not None:
                raise error
            return info if info is not None else load("youtube_video.json")

        monkeypatch.setattr(ytdlp_adapter, "fetch_info", fake_fetch)

    return _install


async def test_analyze_returns_the_full_payload(client, stub_extractor):
    stub_extractor()
    response = await client.post("/api/analyze", json={"url": YOUTUBE_URL})
    assert response.status_code == 200

    body = response.json()
    assert body["platform"] == "youtube"
    assert body["platform_name"] == "YouTube"
    assert body["content_type"] == "video"

    extraction = body["extraction"]
    assert extraction["metadata"]["title"].startswith("Big Buck Bunny")
    assert extraction["metadata"]["author"] == "Blender Foundation"
    assert len(extraction["formats"]) == 6
    assert len(extraction["thumbnails"]) == 5
    assert len(extraction["subtitles"]) == 4
    assert extraction["text"]["hashtags"] == ["Blender", "OpenMovie", "b3d"]


async def test_capabilities_come_from_the_registry(client, stub_extractor):
    stub_extractor()
    body = (await client.post("/api/analyze", json={"url": YOUTUBE_URL})).json()
    capabilities = {c["capability"] for c in body["capabilities"]}
    assert {"video", "audio", "thumbnail", "subtitles", "metadata"} <= capabilities
    assert all(c["support"] == "full" for c in body["capabilities"])


async def test_known_limitations_are_surfaced(client, stub_extractor):
    """Section 4: be honest in the UI about what does not work."""
    stub_extractor()
    body = (await client.post("/api/analyze", json={"url": YOUTUBE_URL})).json()
    assert body["known_limitations"]
    assert any("age-restricted" in limit.lower() for limit in body["known_limitations"])


@pytest.mark.parametrize(
    ("url", "expected_code", "status"),
    [
        ("", "INVALID_URL", 422),
        ("not a url", "UNSUPPORTED_PLATFORM", 400),
        ("https://vimeo.com/12345", "UNSUPPORTED_PLATFORM", 400),
        ("https://www.youtube.com/@channel", "UNSUPPORTED_CONTENT_TYPE", 400),
        ("https://www.instagram.com/reel/Cabc123def/", "UNSUPPORTED_PLATFORM", 400),
        ("https://www.snapchat.com/spotlight/abc123", "UNSUPPORTED_PLATFORM", 400),
    ],
)
async def test_rejections_carry_a_taxonomy_code(client, url, expected_code, status):
    response = await client.post("/api/analyze", json={"url": url})
    assert response.status_code == status
    body = response.json()["error"]
    assert body["code"] == expected_code
    # Every rejection is actionable -- never a bare failure.
    assert body["message"] and body["action"]


async def test_detection_happens_before_any_extraction(client, stub_extractor):
    """An unsupported URL must not cost a subprocess and a platform round trip."""
    called = False

    from app.extractors import ytdlp_adapter

    async def fake_fetch(url: str, *, timeout: int) -> dict:
        nonlocal called
        called = True
        return load("youtube_video.json")

    import pytest as _pytest

    monkeypatch = _pytest.MonkeyPatch()
    monkeypatch.setattr(ytdlp_adapter, "fetch_info", fake_fetch)
    try:
        await client.post("/api/analyze", json={"url": "https://vimeo.com/12345"})
    finally:
        monkeypatch.undo()

    assert called is False


async def test_drm_content_is_refused(client, stub_extractor):
    stub_extractor(load("youtube_drm.json"))
    response = await client.post("/api/analyze", json={"url": YOUTUBE_URL})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DRM_PROTECTED"


async def test_live_streams_are_refused(client, stub_extractor):
    info = load("youtube_video.json")
    info["live_status"] = "is_live"
    stub_extractor(info)

    response = await client.post("/api/analyze", json={"url": YOUTUBE_URL})
    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "UNSUPPORTED_CONTENT_TYPE"
    assert "live" in (body["detail"] or "").lower()


async def test_age_restricted_content_is_refused(client, stub_extractor):
    """We never use anyone's session, so age-gated content cannot be served."""
    info = load("youtube_video.json")
    info["age_limit"] = 18
    stub_extractor(info)

    response = await client.post("/api/analyze", json={"url": YOUTUBE_URL})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "LOGIN_REQUIRED"


async def test_content_with_no_formats_is_refused(client, stub_extractor):
    info = load("youtube_video.json")
    info["formats"] = []
    stub_extractor(info)

    response = await client.post("/api/analyze", json={"url": YOUTUBE_URL})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNSUPPORTED_CONTENT_TYPE"


@pytest.mark.parametrize(
    "code",
    [
        ErrorCode.PRIVATE_CONTENT,
        ErrorCode.GEOBLOCKED,
        ErrorCode.CONTENT_REMOVED,
        ErrorCode.EXTRACTOR_OUTDATED,
        ErrorCode.UPSTREAM_TIMEOUT,
    ],
)
async def test_extractor_failures_reach_the_client_intact(client, stub_extractor, code):
    stub_extractor(error=AppError(code))
    response = await client.post("/api/analyze", json={"url": YOUTUBE_URL})
    body = response.json()["error"]
    assert body["code"] == str(code)
    assert body["request_id"]


async def test_extractor_outdated_explains_itself(client, stub_extractor):
    """Section 10 singles this one out: the user should understand it is
    temporary and not their fault."""
    stub_extractor(error=AppError(ErrorCode.EXTRACTOR_OUTDATED))
    response = await client.post("/api/analyze", json={"url": YOUTUBE_URL})
    assert response.status_code == 503
    body = response.json()["error"]
    assert "changed" in body["message"].lower()
    assert body["retryable"] is True


async def test_oversized_url_is_rejected_by_validation(client):
    response = await client.post("/api/analyze", json={"url": "https://youtu.be/" + "x" * 5000})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_URL"


async def test_rate_limit_headers_are_present(client, stub_extractor):
    stub_extractor()
    response = await client.post("/api/analyze", json={"url": YOUTUBE_URL})
    assert "X-RateLimit-Limit" in response.headers
    assert "X-RateLimit-Remaining" in response.headers


async def test_rate_limited_requests_are_rejected(client, stub_extractor, monkeypatch):
    from app.config import Settings
    from tests.fakes import FakeRedis

    stub_extractor()
    # One shared instance: a fresh fake per call would reset the counter and
    # the limit would never be reached.
    redis = FakeRedis()
    monkeypatch.setattr("app.core.ratelimit.get_redis", lambda: redis)
    monkeypatch.setattr(
        "app.api.analyze.get_settings",
        lambda: Settings(
            ip_hash_salt="test-salt-value", rate_limit_per_minute=2, rate_limit_per_day=100
        ),
    )

    for _ in range(2):
        assert (await client.post("/api/analyze", json={"url": YOUTUBE_URL})).status_code == 200

    response = await client.post("/api/analyze", json={"url": YOUTUBE_URL})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    # Retry-After must survive the error path: without it a client just retries
    # immediately, which is the behaviour the limit exists to prevent.
    assert int(response.headers["Retry-After"]) > 0
    assert response.headers["X-RateLimit-Remaining"] == "0"


async def test_response_never_echoes_the_source_url(client, stub_extractor):
    """Privacy: the URL is not stored and should not come back either."""
    stub_extractor()
    response = await client.post("/api/analyze", json={"url": YOUTUBE_URL})
    assert "watch?v=aqz-KE-bpKQ" not in response.text


async def test_registry_endpoint_lists_only_enabled_platforms(client):
    body = (await client.get("/api/registry")).json()
    platforms = {p["platform"] for p in body["platforms"]}
    assert "snapchat" not in platforms
    assert body["implemented"] == ["youtube"]


async def test_registry_reports_support_levels(client):
    body = (await client.get("/api/registry")).json()
    linkedin = next(p for p in body["platforms"] if p["platform"] == "linkedin")
    supports = {
        c["support"] for infos in linkedin["content_types"].values() for c in infos
    }
    assert "fragile" in supports
    notes = [c["note"] for infos in linkedin["content_types"].values() for c in infos]
    assert all(n for n in notes if n is not None)


def test_fixture_normalises_without_error():
    """Guards the fixture itself: a malformed fixture would make every test
    above meaningless."""
    result = normalise(load("youtube_video.json"))
    assert result.formats and result.thumbnails and result.subtitles
