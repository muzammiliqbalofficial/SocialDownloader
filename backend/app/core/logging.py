"""Structured logging.

Two rules that are not negotiable (section 6):
  * every record carries the request ID, so a user-reported failure is traceable;
  * source URLs never reach the logs at INFO. We log platform and content type
    instead. `redact_url` exists so that the rule is easy to follow rather than
    something each call site has to remember.
"""

from __future__ import annotations

import hashlib
import logging
import sys
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlsplit

import structlog

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


def bind_request_id(request_id: str | None) -> None:
    request_id_ctx.set(request_id)


def get_request_id() -> str | None:
    return request_id_ctx.get()


def _add_request_id(_logger: Any, _name: str, event_dict: dict) -> dict:
    rid = request_id_ctx.get()
    if rid:
        event_dict.setdefault("request_id", rid)
    return event_dict


def redact_url(url: str | None) -> str | None:
    """Reduce a URL to host + a short digest of the path.

    Enough to correlate repeated failures on the same content, not enough to
    reconstruct what a given person downloaded.
    """
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return "invalid-url"
    host = parts.netloc or "unknown"
    remainder = f"{parts.path}?{parts.query}" if parts.query else parts.path
    digest = hashlib.sha256(remainder.encode("utf-8")).hexdigest()[:12]
    return f"{host}/#{digest}"


def configure_logging(*, level: str = "INFO", json_logs: bool = True) -> None:
    """Idempotent: safe to call from the API, the worker and the test suite."""
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _add_request_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Route uvicorn/sqlalchemy stdlib records through the same handler so the
    # output stream stays uniformly parseable.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.getLevelNamesMapping()[level.upper()],
        force=True,
    )
    for noisy in ("uvicorn.access", "uvicorn.error", "sqlalchemy.engine"):
        logging.getLogger(noisy).handlers.clear()
        logging.getLogger(noisy).propagate = True


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
