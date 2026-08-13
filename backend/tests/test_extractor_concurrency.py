"""The extraction concurrency guard.

Each yt-dlp subprocess costs ~45 MiB RSS at minimum. Cloud Run's default
container concurrency is 80, which would be multiple gigabytes of resident
memory and a guaranteed OOM. No functional test surfaces that, so these tests
exist specifically to pin the bound.

The subprocess is stubbed throughout -- what is under test is the gate, not
yt-dlp.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.config import Settings
from app.core.errors import AppError, ErrorCode
from app.extractors import ytdlp_adapter

FIXTURES = Path(__file__).parent / "fixtures"
YOUTUBE_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"

LIMIT = 4
CALLERS = 20


class ConcurrencyProbe:
    """Stands in for the subprocess and records peak overlap."""

    def __init__(self, duration: float = 0.05) -> None:
        self.duration = duration
        self.current = 0
        self.peak = 0
        self.started = 0

    async def run(self, args: list[str], *, timeout: int) -> tuple[int, bytes, bytes]:
        self.started += 1
        self.current += 1
        self.peak = max(self.peak, self.current)
        try:
            await asyncio.sleep(self.duration)
        finally:
            self.current -= 1
        payload = (FIXTURES / "youtube_video.json").read_bytes()
        return 0, payload, b""


@pytest.fixture
def settings() -> Settings:
    return Settings(
        ip_hash_salt="test-salt-value",
        max_concurrent_extractions=LIMIT,
        extraction_queue_wait_seconds=0.05,
        extraction_busy_retry_after_seconds=5,
        # Keep the per-IP limiter out of the way: it raises the same code, and
        # this test is about the extractor gate specifically.
        rate_limit_per_minute=10_000,
        rate_limit_per_day=10_000,
    )


@pytest.fixture
def probe(monkeypatch, settings) -> ConcurrencyProbe:
    probe = ConcurrencyProbe()
    monkeypatch.setattr(ytdlp_adapter, "_run", probe.run)
    monkeypatch.setattr(ytdlp_adapter, "get_settings", lambda: settings)
    return probe


async def test_semaphore_bounds_actual_subprocess_concurrency(probe):
    results = await asyncio.gather(
        *(ytdlp_adapter.fetch_info(YOUTUBE_URL, timeout=10) for _ in range(CALLERS)),
        return_exceptions=True,
    )

    assert probe.peak <= LIMIT, f"peak concurrency {probe.peak} exceeded the limit {LIMIT}"
    assert probe.peak > 1, "the gate serialised everything; that would be a throughput bug"

    succeeded = [r for r in results if isinstance(r, dict)]
    rejected = [r for r in results if isinstance(r, AppError)]
    assert len(succeeded) + len(rejected) == CALLERS
    assert not [r for r in results if isinstance(r, BaseException) and not isinstance(r, AppError)]


async def test_saturated_callers_are_rejected_rather_than_hung(probe):
    """A bounded wait then a clear 429. The failure mode being avoided is a
    request that sits in a queue until the load balancer kills it."""
    results = await asyncio.gather(
        *(ytdlp_adapter.fetch_info(YOUTUBE_URL, timeout=10) for _ in range(CALLERS)),
        return_exceptions=True,
    )

    rejected = [r for r in results if isinstance(r, AppError)]
    assert rejected, "20 callers against 4 slots with a 50ms wait should reject some"

    for error in rejected:
        assert error.code is ErrorCode.RATE_LIMITED
        assert error.headers.get("Retry-After") == "5"
        assert error.context.get("reason") == "extractor_saturated"


async def test_rejection_is_fast(probe):
    """The whole point of the bounded wait: a rejected caller learns quickly."""
    started = asyncio.get_running_loop().time()
    await asyncio.gather(
        *(ytdlp_adapter.fetch_info(YOUTUBE_URL, timeout=10) for _ in range(CALLERS)),
        return_exceptions=True,
    )
    elapsed = asyncio.get_running_loop().time() - started

    # Generous ceiling; the point is that it is bounded rather than a pile-up.
    assert elapsed < 3.0, f"saturation handling took {elapsed:.2f}s"


async def test_slots_are_returned_after_rejection(probe):
    """Guards against a leaked permit: `asyncio.wait_for` cancelling an
    in-progress `acquire()` must not consume a slot."""
    await asyncio.gather(
        *(ytdlp_adapter.fetch_info(YOUTUBE_URL, timeout=10) for _ in range(CALLERS)),
        return_exceptions=True,
    )
    assert ytdlp_adapter.in_flight_extractions() == 0

    # If permits leaked, this later call would be rejected too.
    result = await ytdlp_adapter.fetch_info(YOUTUBE_URL, timeout=10)
    assert result["id"] == "aqz-KE-bpKQ"


async def test_slots_are_returned_after_an_extraction_error(probe, monkeypatch):
    async def failing_run(args, *, timeout):
        return 1, b"", b"ERROR: [youtube] abc: Private video"

    monkeypatch.setattr(ytdlp_adapter, "_run", failing_run)

    for _ in range(LIMIT * 2):
        with pytest.raises(AppError):
            await ytdlp_adapter.fetch_info(YOUTUBE_URL, timeout=10)

    assert ytdlp_adapter.in_flight_extractions() == 0


async def test_limit_of_one_serialises_completely(monkeypatch):
    settings = Settings(
        ip_hash_salt="test-salt-value",
        max_concurrent_extractions=1,
        extraction_queue_wait_seconds=5.0,
    )
    probe = ConcurrencyProbe(duration=0.01)
    monkeypatch.setattr(ytdlp_adapter, "_run", probe.run)
    monkeypatch.setattr(ytdlp_adapter, "get_settings", lambda: settings)

    await asyncio.gather(*(ytdlp_adapter.fetch_info(YOUTUBE_URL, timeout=10) for _ in range(5)))
    assert probe.peak == 1
    assert probe.started == 5


async def test_a_generous_wait_admits_everyone(monkeypatch):
    """With enough patience nobody is rejected -- the gate throttles, it does
    not drop work on the floor."""
    settings = Settings(
        ip_hash_salt="test-salt-value",
        max_concurrent_extractions=LIMIT,
        extraction_queue_wait_seconds=10.0,
    )
    probe = ConcurrencyProbe(duration=0.01)
    monkeypatch.setattr(ytdlp_adapter, "_run", probe.run)
    monkeypatch.setattr(ytdlp_adapter, "get_settings", lambda: settings)

    results = await asyncio.gather(
        *(ytdlp_adapter.fetch_info(YOUTUBE_URL, timeout=10) for _ in range(CALLERS))
    )
    assert len(results) == CALLERS
    assert probe.peak <= LIMIT


# --------------------------------------------------------------------------
# End to end through the API
# --------------------------------------------------------------------------


async def test_concurrent_analyze_requests_are_bounded_and_rejected_cleanly(
    app, probe, monkeypatch, settings
):
    import httpx

    monkeypatch.setattr("app.api.analyze.get_settings", lambda: settings)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = await asyncio.gather(
            *(
                client.post("/api/analyze", json={"url": YOUTUBE_URL})
                for _ in range(CALLERS)
            )
        )

    assert probe.peak <= LIMIT

    codes = [r.status_code for r in responses]
    assert set(codes) <= {200, 429}
    assert 200 in codes, "some requests should get through"

    busy = [r for r in responses if r.status_code == 429]
    assert busy, "with 20 callers and 4 slots some should be turned away"
    for response in busy:
        body = response.json()["error"]
        assert body["code"] == "RATE_LIMITED"
        assert body["retryable"] is True
        # Without Retry-After a client just retries immediately, which is
        # exactly the stampede the limit exists to prevent.
        assert int(response.headers["Retry-After"]) == 5

    for response in (r for r in responses if r.status_code == 200):
        assert response.json()["extraction"]["metadata"]["title"]


async def test_analyze_recovers_after_a_burst(app, probe, monkeypatch, settings):
    """The instance must not be left wedged once the burst passes."""
    import httpx

    monkeypatch.setattr("app.api.analyze.get_settings", lambda: settings)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await asyncio.gather(
            *(client.post("/api/analyze", json={"url": YOUTUBE_URL}) for _ in range(CALLERS))
        )
        recovery = await client.post("/api/analyze", json={"url": YOUTUBE_URL})

    assert recovery.status_code == 200
    assert ytdlp_adapter.in_flight_extractions() == 0


def test_fixture_payload_is_valid_json():
    assert json.loads((FIXTURES / "youtube_video.json").read_bytes())["id"]
