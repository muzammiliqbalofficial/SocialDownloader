"""Request-scoped middleware: request IDs and access logging."""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.logging import bind_request_id, get_logger

REQUEST_ID_HEADER = "X-Request-ID"
_log = get_logger("http")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request ID to every request, log the outcome, echo the header.

    An inbound X-Request-ID is honoured so a trace can span the frontend and the
    API, but it is length-capped: it ends up in log output and must not become a
    vector for log flooding.
    """

    def __init__(self, app: ASGIApp, *, max_inbound_id_length: int = 64) -> None:
        super().__init__(app)
        self._max_id_length = max_inbound_id_length

    async def dispatch(self, request: Request, call_next) -> Response:
        inbound = request.headers.get(REQUEST_ID_HEADER, "").strip()
        request_id = (
            inbound if inbound and len(inbound) <= self._max_id_length else uuid.uuid4().hex
        )

        bind_request_id(request_id)
        request.state.request_id = request_id
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            # The exception handlers own the response body; we only record the
            # timing here and re-raise so they still run.
            _log.exception(
                "request.failed",
                method=request.method,
                path=request.url.path,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            bind_request_id(None)
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id

        # Note: path only, never the query string -- a source URL arrives as a
        # query parameter or body field on several routes.
        log = _log.warning if response.status_code >= 500 else _log.info
        log(
            "request.completed",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        bind_request_id(None)
        return response
