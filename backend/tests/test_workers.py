"""The TTL sweeper (section 2.5). No database required -- the DB half is
allowed to fail independently, and this asserts that it does."""

from __future__ import annotations

import os
import time

from app.workers.tasks import cleanup_expired, ping


async def test_ping_returns_pong():
    assert await ping({}) == "pong"


async def test_sweeper_deletes_files_older_than_the_ttl(tmp_path, monkeypatch):
    from app.config import Settings, get_settings

    settings = Settings(
        ip_hash_salt="test-salt-value", tmp_dir=str(tmp_path), media_ttl_seconds=900
    )
    get_settings.cache_clear()
    monkeypatch.setattr("app.workers.tasks.get_settings", lambda: settings)

    stale = tmp_path / "stale.mp4"
    stale.write_bytes(b"old media")
    old = time.time() - 1000
    os.utime(stale, (old, old))

    fresh = tmp_path / "fresh.mp4"
    fresh.write_bytes(b"new media")

    stale_dir = tmp_path / "job-abc"
    stale_dir.mkdir()
    (stale_dir / "part.mp4").write_bytes(b"partial")
    os.utime(stale_dir, (old, old))

    result = await cleanup_expired()

    assert not stale.exists()
    assert not stale_dir.exists()
    assert fresh.exists(), "files inside the TTL must survive"
    assert result["removed_files"] == 2


async def test_sweeper_survives_a_database_outage(tmp_path, monkeypatch):
    """File deletion carries the compliance obligation, so it must not be
    rolled back by an unrelated Postgres failure."""
    from app.config import Settings

    settings = Settings(ip_hash_salt="test-salt-value", tmp_dir=str(tmp_path))
    monkeypatch.setattr("app.workers.tasks.get_settings", lambda: settings)

    stale = tmp_path / "stale.mp4"
    stale.write_bytes(b"old")
    old = time.time() - 1000
    os.utime(stale, (old, old))

    # No engine is initialised in the test process, so session_scope raises.
    result = await cleanup_expired()

    assert not stale.exists()
    assert result["expired_jobs"] == 0


async def test_sweeper_tolerates_a_missing_tmp_dir(monkeypatch, tmp_path):
    from app.config import Settings

    settings = Settings(ip_hash_salt="test-salt-value", tmp_dir=str(tmp_path / "nope"))
    monkeypatch.setattr("app.workers.tasks.get_settings", lambda: settings)
    assert (await cleanup_expired())["removed_files"] == 0
