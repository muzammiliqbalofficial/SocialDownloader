"""arq worker entrypoint: `arq app.workers.settings.WorkerSettings`."""

from __future__ import annotations

import logging

from arq.connections import RedisSettings
from arq.cron import cron

from app.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.models.db import dispose_engine, init_engine
from app.workers.tasks import cleanup_expired, ping

_log = get_logger("worker")


async def startup(ctx: dict) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.use_json_logs)

    # arq's CLI installs its own handler on the "arq" logger and leaves
    # propagation on, so every worker line would otherwise be emitted twice --
    # once by arq's handler and once by the root handler configure_logging set
    # up. This runs after arq's dictConfig, so it is the point where it sticks.
    logging.getLogger("arq").propagate = False

    init_engine(settings)
    _log.info("worker.startup", environment=settings.environment)


async def shutdown(ctx: dict) -> None:
    await dispose_engine()
    _log.info("worker.shutdown")


class WorkerSettings:
    functions = [ping, cleanup_expired]
    # Every two minutes: well inside the 15-minute media TTL even if one run is
    # skipped because the worker was busy.
    cron_jobs = [cron(cleanup_expired, minute=set(range(0, 60, 2)), run_at_startup=True)]
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 4
    job_timeout = get_settings().job_timeout_seconds
    keep_result = 300
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
