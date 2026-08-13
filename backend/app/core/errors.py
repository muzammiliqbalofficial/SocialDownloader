"""The single error taxonomy (section 10).

This module is the source of truth for both sides of the wire:
`scripts/gen_error_codes.py` renders `frontend/lib/errors.ts` from the catalog
below, and `tests/test_error_codes_sync.py` fails if the two drift apart.

Every code carries a user-facing message and a suggested action, so no failure
can reach the UI as a bare "something went wrong".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    INVALID_URL = "INVALID_URL"
    UNSUPPORTED_PLATFORM = "UNSUPPORTED_PLATFORM"
    UNSUPPORTED_CONTENT_TYPE = "UNSUPPORTED_CONTENT_TYPE"
    PRIVATE_CONTENT = "PRIVATE_CONTENT"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    DRM_PROTECTED = "DRM_PROTECTED"
    GEOBLOCKED = "GEOBLOCKED"
    CONTENT_REMOVED = "CONTENT_REMOVED"
    RATE_LIMITED = "RATE_LIMITED"
    EXTRACTOR_OUTDATED = "EXTRACTOR_OUTDATED"
    DURATION_EXCEEDED = "DURATION_EXCEEDED"
    FILESIZE_EXCEEDED = "FILESIZE_EXCEEDED"
    AI_UNAVAILABLE = "AI_UNAVAILABLE"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True)
class ErrorSpec:
    status: int
    message: str
    action: str
    # Whether a retry of the identical request could plausibly succeed later.
    retryable: bool = False


ERROR_CATALOG: dict[ErrorCode, ErrorSpec] = {
    ErrorCode.INVALID_URL: ErrorSpec(
        status=400,
        message="That doesn't look like a valid link.",
        action="Check the URL and paste it again, including the https:// prefix.",
    ),
    ErrorCode.UNSUPPORTED_PLATFORM: ErrorSpec(
        status=400,
        message="We don't support this site.",
        action="Supported platforms are YouTube, Instagram, Facebook, LinkedIn and Snapchat.",
    ),
    ErrorCode.UNSUPPORTED_CONTENT_TYPE: ErrorSpec(
        status=400,
        message="This kind of content isn't supported on that platform yet.",
        action="Try a direct link to a video, reel or post.",
    ),
    ErrorCode.PRIVATE_CONTENT: ErrorSpec(
        status=403,
        message="This content is private.",
        action="We only handle publicly accessible content. Ask the owner to make it public.",
    ),
    ErrorCode.LOGIN_REQUIRED: ErrorSpec(
        status=403,
        message="This content requires being signed in to the platform.",
        action=(
            "We don't sign in on your behalf. Only public content that loads in a "
            "logged-out browser can be processed."
        ),
    ),
    ErrorCode.DRM_PROTECTED: ErrorSpec(
        status=403,
        message="This stream is DRM-protected.",
        action="We will not attempt to bypass DRM. Nothing can be downloaded from this link.",
    ),
    ErrorCode.GEOBLOCKED: ErrorSpec(
        status=451,
        message="This content isn't available in the region our servers run from.",
        action="Nothing to retry here -- the platform is blocking the request by location.",
    ),
    ErrorCode.CONTENT_REMOVED: ErrorSpec(
        status=404,
        message="This content no longer exists.",
        action="It was deleted or made unavailable by its owner.",
    ),
    ErrorCode.RATE_LIMITED: ErrorSpec(
        status=429,
        message="You're going a bit fast for us.",
        action="Wait a minute and try again.",
        retryable=True,
    ),
    ErrorCode.EXTRACTOR_OUTDATED: ErrorSpec(
        status=503,
        message="This platform changed recently and we're updating support.",
        action="This usually resolves within a day or two. Please try again later.",
        retryable=True,
    ),
    ErrorCode.DURATION_EXCEEDED: ErrorSpec(
        status=413,
        message="This media is longer than the limit for that action.",
        action="Try a shorter clip, or use a different action that has no duration cap.",
    ),
    ErrorCode.FILESIZE_EXCEEDED: ErrorSpec(
        status=413,
        message="This file is larger than we can process.",
        action="Pick a lower resolution or an audio-only format.",
    ),
    ErrorCode.AI_UNAVAILABLE: ErrorSpec(
        status=503,
        message="AI features aren't enabled on this server.",
        action="Everything except the AI tools still works normally.",
    ),
    ErrorCode.UPSTREAM_TIMEOUT: ErrorSpec(
        status=504,
        message="The platform took too long to respond.",
        action="Try again in a moment.",
        retryable=True,
    ),
    ErrorCode.INTERNAL_ERROR: ErrorSpec(
        status=500,
        message="Something broke on our side.",
        action="Try again. If it keeps happening, the request ID below helps us trace it.",
        retryable=True,
    ),
}


class AppError(Exception):
    """Every deliberate failure path raises this, never a bare HTTPException.

    `detail` is optional extra context shown to the user *in addition to* the
    catalog message (for example, which limit was exceeded). It must never
    contain a source URL, a cookie, or anything else user-identifying.
    """

    def __init__(
        self,
        code: ErrorCode,
        *,
        detail: str | None = None,
        context: dict[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        self.code = code
        self.spec = ERROR_CATALOG[code]
        self.detail = detail
        self.context = context or {}
        super().__init__(f"{code}: {detail or self.spec.message}")
        if cause is not None:
            self.__cause__ = cause

    @property
    def status_code(self) -> int:
        return self.spec.status

    def to_payload(self, request_id: str | None = None) -> dict[str, Any]:
        return {
            "error": {
                "code": str(self.code),
                "message": self.spec.message,
                "action": self.spec.action,
                "detail": self.detail,
                "retryable": self.spec.retryable,
                "request_id": request_id,
            }
        }


def error_payload(
    code: ErrorCode, *, detail: str | None = None, request_id: str | None = None
) -> dict[str, Any]:
    return AppError(code, detail=detail).to_payload(request_id)
