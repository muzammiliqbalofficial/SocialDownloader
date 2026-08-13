"""yt-dlp stderr -> error taxonomy.

The strings below are real yt-dlp output, not invented. When yt-dlp changes its
wording these tests are the thing that should fail first.
"""

from __future__ import annotations

import pytest

from app.core.errors import ErrorCode
from app.extractors.errors_map import is_recognised, map_stderr

CASES: list[tuple[str, ErrorCode]] = [
    (
        "ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video",
        ErrorCode.PRIVATE_CONTENT,
    ),
    (
        "ERROR: [youtube] abc: Sign in to confirm your age. "
        "This video may be inappropriate for some users.",
        ErrorCode.LOGIN_REQUIRED,
    ),
    (
        "ERROR: [youtube] abc: Sign in to confirm you're not a bot. Use --cookies-from-browser",
        ErrorCode.LOGIN_REQUIRED,
    ),
    (
        "ERROR: [youtube] abc: Join this channel to get access to members-only content",
        ErrorCode.LOGIN_REQUIRED,
    ),
    (
        "ERROR: [youtube] abc: This video is only available to Music Premium members",
        ErrorCode.LOGIN_REQUIRED,
    ),
    (
        "ERROR: [generic] abc: This video is DRM protected",
        ErrorCode.DRM_PROTECTED,
    ),
    (
        "ERROR: [youtube] abc: The uploader has not made this video available in your country",
        ErrorCode.GEOBLOCKED,
    ),
    (
        "ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader",
        ErrorCode.CONTENT_REMOVED,
    ),
    (
        "ERROR: [youtube] abc: This video has been removed for violating YouTube's policy",
        ErrorCode.CONTENT_REMOVED,
    ),
    (
        "ERROR: Unsupported URL: https://example.com/nope",
        ErrorCode.UNSUPPORTED_CONTENT_TYPE,
    ),
    (
        "ERROR: [youtube] abc: This live event will begin in 3 hours",
        ErrorCode.UNSUPPORTED_CONTENT_TYPE,
    ),
    (
        "ERROR: Unable to download webpage: HTTP Error 429: Too Many Requests",
        ErrorCode.RATE_LIMITED,
    ),
    (
        "ERROR: Unable to download webpage: <urlopen error timed out>",
        ErrorCode.UPSTREAM_TIMEOUT,
    ),
    (
        "ERROR: Unable to download webpage: HTTP Error 503: Service Unavailable",
        ErrorCode.UPSTREAM_TIMEOUT,
    ),
    (
        "ERROR: [youtube] abc: Unable to extract yt initial data; please report this issue",
        ErrorCode.EXTRACTOR_OUTDATED,
    ),
    (
        "ERROR: [youtube] abc: Failed to parse JSON (caused by JSONDecodeError)",
        ErrorCode.EXTRACTOR_OUTDATED,
    ),
]


@pytest.mark.parametrize(("stderr", "expected"), CASES)
def test_known_failures_map_to_the_right_code(stderr, expected):
    assert map_stderr(stderr) is expected
    assert is_recognised(stderr)


def test_unknown_failure_defaults_to_extractor_outdated():
    """An unrecognised failure is far more likely to be platform drift than a
    bug in our code, and 'we're updating support' is the more useful message."""
    stderr = "ERROR: [youtube] abc: something nobody has ever seen before"
    assert map_stderr(stderr) is ErrorCode.EXTRACTOR_OUTDATED
    assert not is_recognised(stderr)


def test_empty_stderr_defaults_safely():
    assert map_stderr("") is ErrorCode.EXTRACTOR_OUTDATED
    assert map_stderr(None) is ErrorCode.EXTRACTOR_OUTDATED  # type: ignore[arg-type]


def test_proxy_failure_is_transport_not_platform_drift():
    """Observed for real when the sandbox proxy refused the tunnel. Without a
    transport rule this lands in the extractor bucket and tells the user the
    platform changed, which sends them after the wrong problem."""
    stderr = (
        "ERROR: [youtube] abc: Unable to download API page: <urlopen error Tunnel "
        "connection failed: 403 Forbidden> (caused by ProxyError(...))"
    )
    assert map_stderr(stderr) is ErrorCode.UPSTREAM_TIMEOUT


def test_drm_wins_over_other_matches():
    """A DRM failure must never be reported as something retryable -- the user
    would keep trying at a wall."""
    stderr = "ERROR: Video unavailable: this stream is DRM protected and cannot be downloaded"
    assert map_stderr(stderr) is ErrorCode.DRM_PROTECTED


def test_matching_is_case_insensitive():
    assert map_stderr("ERROR: PRIVATE VIDEO") is ErrorCode.PRIVATE_CONTENT


@pytest.mark.parametrize("code", [c for _, c in CASES])
def test_mapped_codes_all_exist_in_the_taxonomy(code):
    assert code in ErrorCode
