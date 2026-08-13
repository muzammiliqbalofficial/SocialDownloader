"""Tests that need a real Postgres.

Skipped unless TEST_DATABASE_URL is set, so the default suite stays offline.
CI sets it against a service container; locally, `make test-db` starts one.

These exist because the constraints in the migration are only meaningful if the
database actually enforces them -- asserting on the SQLAlchemy objects proves
nothing about what Postgres does.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models.job import Job, JobStatus
from app.models.usage import UsageDaily

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set; skipping database integration tests"
)


@pytest.fixture
async def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.models.base import Base

    eng = create_async_engine(TEST_DATABASE_URL, poolclass=None)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s


async def test_job_round_trips(session):
    job = Job(
        id=uuid.uuid4(),
        platform="youtube",
        content_type="video",
        action="download",
        requested_format="137+140",
        ip_hash="a" * 64,
        url_hash="b" * 32,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    session.add(job)
    await session.commit()

    stored = (await session.execute(select(Job).where(Job.id == job.id))).scalar_one()
    assert stored.status == JobStatus.QUEUED
    assert stored.progress == 0
    assert stored.created_at is not None


async def test_database_rejects_out_of_range_progress(session):
    session.add(
        Job(
            id=uuid.uuid4(),
            platform="youtube",
            content_type="video",
            action="download",
            progress=150,
        )
    )
    with pytest.raises((IntegrityError, DBAPIError)):
        await session.commit()


async def test_database_rejects_an_unknown_status(session):
    session.add(
        Job(
            id=uuid.uuid4(),
            platform="youtube",
            content_type="video",
            action="download",
            status="teleported",
        )
    )
    with pytest.raises((IntegrityError, DBAPIError)):
        await session.commit()


async def test_usage_bucket_is_unique_per_day_and_action(session):
    today = datetime.now(UTC).date()
    for _ in range(2):
        session.add(
            UsageDaily(
                day=today, platform="youtube", content_type="video", action="download", count=1
            )
        )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_expiry_index_is_present(session):
    """The sweeper depends on this index; without it the scan degrades as the
    table grows."""
    rows = await session.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'jobs'")
    )
    assert "ix_jobs_expires_at" in {r[0] for r in rows}


async def test_health_reports_ok_against_a_live_database(engine, monkeypatch):
    import httpx

    from app.api import health
    from app.config import Settings
    from app.main import create_app
    from app.models import db as db_module
    from app.models.schemas import ComponentHealth

    monkeypatch.setattr(db_module, "_engine", engine)

    async def redis_ok() -> ComponentHealth:
        return ComponentHealth(status="ok")

    monkeypatch.setattr(health, "_check_redis", redis_ok)

    settings = Settings(ip_hash_salt="test-salt-value", environment="test")
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        body = (await ac.get("/api/health")).json()

    assert body["status"] == "ok"
    assert body["components"]["database"]["status"] == "ok"
