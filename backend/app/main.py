"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.router import api_router
from app.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode, error_payload
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.core.redis import close_redis, init_redis
from app.models.db import dispose_engine, init_engine

_log = get_logger("app")

# Maps HTTP statuses raised by the framework itself onto our taxonomy, so a
# 404 from routing looks the same to the client as a 404 from a handler.
_HTTP_STATUS_TO_CODE = {
    404: ErrorCode.CONTENT_REMOVED,
    405: ErrorCode.UNSUPPORTED_CONTENT_TYPE,
    413: ErrorCode.FILESIZE_EXCEEDED,
    429: ErrorCode.RATE_LIMITED,
    504: ErrorCode.UPSTREAM_TIMEOUT,
}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.use_json_logs)

    # Connections are created lazily by the pools; nothing here reaches out to
    # the network, so the app still boots when Postgres is slow to start and
    # reports itself unhealthy instead of crash-looping.
    init_engine(settings)
    init_redis(settings)

    _log.info(
        "app.startup",
        environment=settings.environment,
        version=settings.app_version,
        transcription=settings.transcription_enabled,
        summarization=settings.summarization_enabled,
    )
    try:
        yield
    finally:
        await dispose_engine()
        await close_redis()
        _log.info("app.shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.use_json_logs)

    app = FastAPI(
        title="SocialDownloader API",
        version=settings.app_version,
        description=(
            "Extraction API for publicly accessible social media content. "
            "Public content only; no DRM circumvention; no credential storage."
        ),
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Content-Disposition"],
    )

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        rid = _request_id(request)
        log = _log.error if exc.status_code >= 500 else _log.info
        log("error.app", code=str(exc.code), status=exc.status_code, **exc.context)
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload(rid))

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_STATUS_TO_CODE.get(exc.status_code)
        if code is None:
            code = ErrorCode.INVALID_URL if exc.status_code < 500 else ErrorCode.INTERNAL_ERROR
        payload = error_payload(code, request_id=_request_id(request))
        # Preserve the framework's status so routing 404s stay 404s even where
        # the mapped code's canonical status differs.
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", ())[1:]) or "request"
        return JSONResponse(
            status_code=422,
            content=error_payload(
                ErrorCode.INVALID_URL,
                detail=f"Invalid value for '{field}'.",
                request_id=_request_id(request),
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Deliberately no exception text in the response: stack traces and
        # driver errors can leak internal hostnames and source URLs.
        _log.exception("error.unhandled", exc_type=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content=error_payload(ErrorCode.INTERNAL_ERROR, request_id=_request_id(request)),
        )

    app.include_router(api_router)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": "socialdownloader-api", "version": settings.app_version}

    return app


app = create_app()
