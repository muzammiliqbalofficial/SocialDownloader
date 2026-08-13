"""POST /api/analyze -- metadata only, never downloads (section 6)."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger
from app.core.ratelimit import apply_headers, enforce
from app.extractors import ytdlp_adapter
from app.models.extraction import ExtractionResult
from app.platforms.registry import Detection, detect

router = APIRouter(tags=["analyze"])
_log = get_logger("analyze")

# Long enough for a pathological share link, short enough that the field is not
# a denial-of-service vector.
MAX_URL_LENGTH = 2048


class AnalyzeRequest(BaseModel):
    url: str = Field(min_length=1, max_length=MAX_URL_LENGTH)


class CapabilityPayload(BaseModel):
    capability: str
    support: str
    note: str | None = None


class AnalyzeResponse(BaseModel):
    platform: str
    platform_name: str
    content_type: str
    capabilities: list[CapabilityPayload]
    # Surfaced so the UI can be honest about fragile platforms (section 4).
    known_limitations: list[str] = Field(default_factory=list)
    extraction: ExtractionResult


def _to_response(detection: Detection, extraction: ExtractionResult) -> AnalyzeResponse:
    return AnalyzeResponse(
        platform=str(detection.platform),
        platform_name=detection.spec.display_name,
        content_type=str(detection.content_type),
        capabilities=[
            CapabilityPayload(
                capability=str(info.capability),
                support=str(info.support),
                note=info.note,
            )
            for info in detection.capabilities
        ],
        known_limitations=list(detection.spec.known_limitations),
        extraction=extraction,
    )


@router.post("/analyze", response_model=AnalyzeResponse, summary="Analyze a public URL")
async def analyze(
    payload: AnalyzeRequest,
    request: Request,
    response: Response,
) -> AnalyzeResponse:
    settings: Settings = get_settings()

    verdict = await enforce(request, action="analyze", settings=settings)
    apply_headers(response, verdict)

    # Detection is pure and cheap: reject an unsupported URL before spending a
    # subprocess and a round trip to the platform on it.
    detection = detect(payload.url, settings)

    started = time.perf_counter()
    extraction = await ytdlp_adapter.extract(
        payload.url, timeout=settings.extractor_timeout_seconds
    )
    duration_ms = round((time.perf_counter() - started) * 1000, 2)

    # A live stream has no final file to produce.
    if extraction.metadata.is_live:
        raise AppError(
            ErrorCode.UNSUPPORTED_CONTENT_TYPE,
            detail="This is a live stream. Try again once the broadcast has ended.",
            context={"platform": str(detection.platform)},
        )

    # Age-gated content requires someone's session, which we never use.
    if extraction.metadata.age_limit >= 18:
        raise AppError(
            ErrorCode.LOGIN_REQUIRED,
            detail="This content is age-restricted and needs a signed-in session.",
            context={"platform": str(detection.platform)},
        )

    if not extraction.formats:
        raise AppError(
            ErrorCode.UNSUPPORTED_CONTENT_TYPE,
            detail="No downloadable media was found at that link.",
            context={"platform": str(detection.platform)},
        )

    # Platform and content type only -- never the URL (section 6).
    _log.info(
        "analyze.completed",
        platform=str(detection.platform),
        content_type=str(detection.content_type),
        format_count=len(extraction.formats),
        subtitle_count=len(extraction.subtitles),
        duration_ms=duration_ms,
    )

    return _to_response(detection, extraction)
