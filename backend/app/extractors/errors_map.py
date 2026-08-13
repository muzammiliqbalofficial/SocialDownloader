"""Translate yt-dlp's stderr into the shared error taxonomy (section 10).

This is the layer that turns "ERROR: [youtube] dQw4w9WgXcQ: Private video" into
`PRIVATE_CONTENT`, and an unrecognised extraction failure into
`EXTRACTOR_OUTDATED` rather than a stack trace.

Rules are ordered: the first matching group wins, so specific patterns come
before catch-alls. Matching is case-insensitive against the whole stderr.

The strings are real yt-dlp output. When yt-dlp rewords a message the mapping
silently degrades to EXTRACTOR_OUTDATED, so `tests/test_ytdlp_errors.py` pins
the wording and is the thing that should fail first.
"""

from __future__ import annotations

import re

from app.core.errors import ErrorCode

# (code, alternative patterns). Order matters -- see the module docstring.
_RULE_SOURCES: tuple[tuple[ErrorCode, tuple[str, ...]], ...] = (
    # DRM first. A DRM failure must never fall through to something retryable
    # that invites the user to keep trying at a wall.
    (
        ErrorCode.DRM_PROTECTED,
        (r"drm[\s-]*protect", r"protected by drm", r"widevine", r"fairplay", r"playready"),
    ),
    # --- Authentication and privacy ---
    (
        ErrorCode.PRIVATE_CONTENT,
        (r"private video", r"this video is private", r"the account is private"),
    ),
    (
        ErrorCode.LOGIN_REQUIRED,
        (
            r"sign in to confirm your age",
            r"age[- ]restricted",
            r"inappropriate for some users",
            r"sign in to confirm (that )?you'?re not a bot",
            r"login required",
            r"requires? (a )?login",
            r"log in to",
            r"sign in to view",
            r"use --cookies",
            r"cookies are needed",
            r"authentication is required",
            r"--cookies-from-browser",
            # Paywalled content is refused outright (constraint 4); to the user
            # this is the same class of problem as needing to sign in.
            r"members[- ]only",
            r"join this channel",
            r"music premium",
            r"subscriber[- ]only",
            r"paid content",
        ),
    ),
    # --- Availability ---
    (
        ErrorCode.GEOBLOCKED,
        (
            # No leading "not": YouTube phrases it "has not made this video
            # available in your country", so anchoring on "not available"
            # misses the real string.
            r"available in your country",
            r"available from your location",
            r"geo[- ]?restricted",
            r"geo[- ]?block",
            r"blocked it in your country",
        ),
    ),
    (
        ErrorCode.CONTENT_REMOVED,
        (
            r"video unavailable",
            r"has been removed",
            r"no longer available",
            r"account associated with this video has been terminated",
            r"this video has been deleted",
            r"content isn'?t available",
        ),
    ),
    (
        ErrorCode.UNSUPPORTED_CONTENT_TYPE,
        (
            r"this live event will begin",
            r"premieres in",
            r"is not currently live",
            r"live stream recording is not available",
        ),
    ),
    # --- Transport ---
    (ErrorCode.RATE_LIMITED, (r"http error 429", r"too many requests", r"rate[- ]?limit")),
    (
        ErrorCode.UPSTREAM_TIMEOUT,
        (
            r"timed out",
            r"timeout",
            r"connection reset",
            r"temporary failure in name resolution",
            r"network is unreachable",
            r"http error 5\d\d",
            # Our network or proxy, not the platform's page. Without these the
            # failure lands in the extractor bucket and we would tell the user
            # the platform changed, sending them after the wrong problem.
            r"urlopen error",
            r"proxyerror",
            r"tunnel connection failed",
            r"connection refused",
            r"certificate verify failed",
        ),
    ),
    # --- Input ---
    (ErrorCode.UNSUPPORTED_CONTENT_TYPE, (r"unsupported url",)),
    (ErrorCode.INVALID_URL, (r"is not a valid url", r"invalid url")),
    # --- Extractor breakage. Last: the residual bucket for anything that looks
    # like yt-dlp failing to parse a page it used to understand. ---
    (
        ErrorCode.EXTRACTOR_OUTDATED,
        (
            r"unable to extract",
            r"failed to parse",
            r"nsig extraction failed",
            r"unable to download (api page|webpage)",
            r"player response",
            r"signature extraction failed",
            r"http error 4\d\d",
        ),
    ),
)

_RULES: tuple[tuple[re.Pattern[str], ErrorCode], ...] = tuple(
    (re.compile("|".join(patterns)), code) for code, patterns in _RULE_SOURCES
)


def map_stderr(stderr: str) -> ErrorCode:
    """Best-effort classification of a yt-dlp failure.

    Defaults to EXTRACTOR_OUTDATED rather than INTERNAL_ERROR: if yt-dlp exited
    non-zero and nothing matched, the likeliest explanation by far is that a
    platform changed and our understanding of it is stale. That is also the
    more useful message, because it tells the user the problem is temporary and
    not their fault.
    """
    haystack = (stderr or "").lower()
    for pattern, code in _RULES:
        if pattern.search(haystack):
            return code
    return ErrorCode.EXTRACTOR_OUTDATED


def is_recognised(stderr: str) -> bool:
    """Whether a rule matched, as opposed to falling through to the default.

    Logged on every failure: a rising rate of unrecognised failures is the
    signal that these rules need updating.
    """
    haystack = (stderr or "").lower()
    return any(pattern.search(haystack) for pattern, _ in _RULES)
