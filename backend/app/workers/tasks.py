"""Worker tasks.

Phase 1 ships only the plumbing plus the TTL sweeper: extraction and download
tasks arrive in Phase 3. The sweeper exists now because the ephemeral-files
constraint (section 2.5) should never be something added later -- if media can
be written at all, something must already be deleting it.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from datetime import UTC, datetime

from sqlalchemy import update

from app.config import get_settings
from app.core.logging import get_logger
from app.models.db import session_scope
from app.models.job import Job, JobStatus

_log = get_logger("worker")


async def ping(ctx: dict) -> str:
    """Trivial task used by the compose healthcheck and the smoke tests."""
    return "pong"


def _sweep_files(tmp_root: str, ttl_seconds: int) -> int:
    """Blocking half of the sweep. Runs in a thread: deleting a part-written
    2 GB file must not stall the worker's event loop."""
    if not os.path.isdir(tmp_root):
        return 0

    cutoff = time.time() - ttl_seconds
    removed = 0
    for entry in os.scandir(tmp_root):
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            if entry.is_dir():
                shutil.rmtree(entry.path, ignore_errors=True)
            else:
                os.unlink(entry.path)
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            _log.warning("cleanup.file_failed", error=type(exc).__name__)
    return removed


async def cleanup_expired(ctx: dict | None = None) -> dict[str, int]:
    """Delete expired scratch files and mark the corresponding jobs expired.

    Runs on a schedule. Idempotent: a second run over the same state is a no-op.
    """
    settings = get_settings()
    now = datetime.now(UTC)

    removed_files = await asyncio.to_thread(
        _sweep_files, settings.tmp_dir, settings.media_ttl_seconds
    )

    expired_jobs = 0
    try:
        async with session_scope() as session:
            result = await session.execute(
                update(Job)
                .where(
                    Job.expires_at.is_not(None),
                    Job.expires_at < now,
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.COMPLETED]),
                )
                .values(status=JobStatus.EXPIRED)
            )
            expired_jobs = result.rowcount or 0
    except Exception as exc:
        # A database blip must not kill the sweeper -- the file deletion above
        # is the part that carries the compliance obligation.
        _log.error("cleanup.db_failed", error=type(exc).__name__)

    if removed_files or expired_jobs:
        _log.info("cleanup.completed", removed_files=removed_files, expired_jobs=expired_jobs)
    return {"removed_files": removed_files, "expired_jobs": expired_jobs}
