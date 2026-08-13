"""Registry and URL detection. No network, no services."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.core.errors import AppError, ErrorCode
from app.platforms.base import Capability, ContentType, Platform, Support
from app.platforms.registry import (
    REGISTRY,
    describe_registry,
    detect,
    enabled_specs,
    platform_for_host,
)

SETTINGS = Settings(ip_hash_salt="test-salt-value")
SNAPCHAT_ON = Settings(ip_hash_salt="test-salt-value", snapchat_enabled=True)


@pytest.mark.parametrize(
    ("url", "content_type", "content_id"),
    [
        ("https://www.youtube.com/watch?v=aqz-KE-bpKQ", ContentType.VIDEO, "aqz-KE-bpKQ"),
        ("https://youtube.com/watch?v=aqz-KE-bpKQ&t=30s", ContentType.VIDEO, "aqz-KE-bpKQ"),
        ("https://youtu.be/aqz-KE-bpKQ", ContentType.VIDEO, "aqz-KE-bpKQ"),
        ("https://www.youtube.com/shorts/abcdefghijk", ContentType.SHORT, "abcdefghijk"),
        ("https://m.youtube.com/watch?v=aqz-KE-bpKQ", ContentType.VIDEO, "aqz-KE-bpKQ"),
        ("https://www.youtube.com/embed/aqz-KE-bpKQ", ContentType.VIDEO, "aqz-KE-bpKQ"),
        ("https://www.youtube.com/live/aqz-KE-bpKQ", ContentType.VIDEO, "aqz-KE-bpKQ"),
        # Bare paste with no scheme -- extremely common.
        ("youtube.com/watch?v=aqz-KE-bpKQ", ContentType.VIDEO, "aqz-KE-bpKQ"),
        ("  https://youtu.be/aqz-KE-bpKQ  ", ContentType.VIDEO, "aqz-KE-bpKQ"),
    ],
)
def test_youtube_urls_are_detected(url, content_type, content_id):
    detection = detect(url, SETTINGS)
    assert detection.platform is Platform.YOUTUBE
    assert detection.content_type is content_type
    assert detection.content_id == content_id


def test_detection_returns_capabilities_for_the_content_type():
    detection = detect("https://youtu.be/aqz-KE-bpKQ", SETTINGS)
    capabilities = {info.capability for info in detection.capabilities}
    assert Capability.VIDEO in capabilities
    assert Capability.SUBTITLES in capabilities
    assert Capability.METADATA in capabilities
    # Instagram-only capability must not appear for YouTube.
    assert Capability.CAROUSEL_ZIP not in capabilities


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "not a url at all",
        "ftp://youtube.com/watch?v=aqz-KE-bpKQ",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://",
    ],
)
def test_malformed_urls_are_rejected_as_invalid(url):
    with pytest.raises(AppError) as excinfo:
        detect(url, SETTINGS)
    assert excinfo.value.code in (ErrorCode.INVALID_URL, ErrorCode.UNSUPPORTED_PLATFORM)


@pytest.mark.parametrize(
    "url",
    [
        "https://vimeo.com/12345",
        "https://tiktok.com/@someone/video/123",
        "https://example.com/watch?v=aqz-KE-bpKQ",
        # Lookalike host: must not be treated as YouTube.
        "https://youtube.com.evil.example/watch?v=aqz-KE-bpKQ",
    ],
)
def test_other_platforms_are_rejected(url):
    with pytest.raises(AppError) as excinfo:
        detect(url, SETTINGS)
    assert excinfo.value.code is ErrorCode.UNSUPPORTED_PLATFORM


def test_youtube_channel_url_is_recognised_but_unsupported():
    with pytest.raises(AppError) as excinfo:
        detect("https://www.youtube.com/@BlenderFoundation", SETTINGS)
    assert excinfo.value.code is ErrorCode.UNSUPPORTED_CONTENT_TYPE
    # The message must say it is a YouTube link we cannot handle, not that
    # YouTube is unsupported.
    assert "YouTube" in (excinfo.value.detail or "")


def test_playlist_is_recognised_but_has_no_capabilities():
    with pytest.raises(AppError) as excinfo:
        detect("https://www.youtube.com/playlist?list=PL1234567890", SETTINGS)
    assert excinfo.value.code is ErrorCode.UNSUPPORTED_CONTENT_TYPE


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://www.instagram.com/reel/Cabc123def/", Platform.INSTAGRAM),
        ("https://www.facebook.com/reel/1234567890", Platform.FACEBOOK),
        ("https://www.linkedin.com/posts/someone_activity-123", Platform.LINKEDIN),
    ],
)
def test_unimplemented_platforms_are_detected_but_refused(url, platform):
    """Detected so the error can be specific, refused because the phase has not
    landed yet."""
    with pytest.raises(AppError) as excinfo:
        detect(url, SETTINGS)
    assert excinfo.value.code is ErrorCode.UNSUPPORTED_PLATFORM
    assert "still being built" in (excinfo.value.detail or "")


def test_snapchat_is_absent_when_disabled():
    """Decision D-004: a disabled platform is omitted entirely, and is
    indistinguishable from an unsupported one."""
    with pytest.raises(AppError) as excinfo:
        detect("https://www.snapchat.com/spotlight/abc123", SETTINGS)
    assert excinfo.value.code is ErrorCode.UNSUPPORTED_PLATFORM
    assert "still being built" not in (excinfo.value.detail or "")
    assert "Snapchat" not in (excinfo.value.detail or "")


def test_error_messages_never_advertise_an_unavailable_platform():
    """Telling someone we support Instagram while Phase 5 is unbuilt is exactly
    the dishonesty section 13 forbids. The list comes from the registry."""
    from app.core.errors import ERROR_CATALOG
    from app.platforms.registry import supported_platform_names

    assert supported_platform_names(SETTINGS) == ["YouTube"]

    # The static catalog entry must not enumerate platforms either.
    action = ERROR_CATALOG[ErrorCode.UNSUPPORTED_PLATFORM].action
    for name in ("Instagram", "Facebook", "LinkedIn", "Snapchat"):
        assert name not in action

    with pytest.raises(AppError) as excinfo:
        detect("https://vimeo.com/12345", SETTINGS)
    detail = excinfo.value.detail or ""
    assert "YouTube" in detail
    for unavailable in ("Instagram", "Facebook", "LinkedIn", "Snapchat"):
        assert unavailable not in detail

    platforms = {p["platform"] for p in describe_registry(SETTINGS)}
    assert "snapchat" not in platforms


def test_snapchat_appears_when_enabled():
    platforms = {p["platform"] for p in describe_registry(SNAPCHAT_ON)}
    assert "snapchat" in platforms
    assert Platform.SNAPCHAT in {s.platform for s in enabled_specs(SNAPCHAT_ON)}


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("www.youtube.com", Platform.YOUTUBE),
        ("YOUTUBE.COM", Platform.YOUTUBE),
        ("music.youtube.com", Platform.YOUTUBE),
        ("youtu.be", Platform.YOUTUBE),
        ("user:pass@youtube.com", Platform.YOUTUBE),
        ("youtube.com:443", Platform.YOUTUBE),
        ("evil.com", None),
        ("notyoutube.com", None),
    ],
)
def test_host_resolution(host, expected):
    assert platform_for_host(host) is expected


def test_registry_describes_only_enabled_platforms():
    described = describe_registry(SETTINGS)
    assert {p["platform"] for p in described} == {
        "youtube",
        "instagram",
        "facebook",
        "linkedin",
    }


def test_only_youtube_is_implemented_in_phase_two():
    described = describe_registry(SETTINGS)
    implemented = {p["platform"] for p in described if p["implemented"]}
    assert implemented == {"youtube"}


def test_every_degraded_capability_explains_itself():
    """Section 4: never a generic failure. A best-effort or fragile capability
    must carry a note the UI can show."""
    for spec in REGISTRY.values():
        for infos in spec.capabilities.values():
            for info in infos:
                if info.support in (Support.BEST_EFFORT, Support.FRAGILE):
                    assert info.note, f"{spec.platform}.{info.capability} has no note"


def test_linkedin_is_marked_fragile_throughout():
    spec = REGISTRY[Platform.LINKEDIN]
    supports = {info.support for infos in spec.capabilities.values() for info in infos}
    assert Support.FRAGILE in supports
    assert Support.FULL not in supports


def test_capability_info_rejects_an_unexplained_degraded_level():
    from app.platforms.base import CapabilityInfo

    with pytest.raises(ValueError, match="must carry a note"):
        CapabilityInfo(Capability.VIDEO, Support.FRAGILE)
