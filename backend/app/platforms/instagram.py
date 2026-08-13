"""Instagram. Detected from Phase 2; implemented in Phase 5."""

from __future__ import annotations

import re

from app.platforms.base import (
    Capability,
    CapabilityInfo,
    ContentType,
    Platform,
    PlatformSpec,
    Support,
    URLPattern,
)

_SHORTCODE = r"(?P<id>[A-Za-z0-9_-]{5,})"

URL_PATTERNS: tuple[URLPattern, ...] = (
    URLPattern(re.compile(rf"/reels?/{_SHORTCODE}"), ContentType.REEL),
    URLPattern(re.compile(rf"/tv/{_SHORTCODE}"), ContentType.VIDEO),
    URLPattern(re.compile(rf"/p/{_SHORTCODE}"), ContentType.POST),
    URLPattern(re.compile(r"/stories/(?P<id>[A-Za-z0-9_.]+)"), ContentType.STORY),
)

_POST_CAPABILITIES: tuple[CapabilityInfo, ...] = (
    CapabilityInfo(Capability.THUMBNAIL, Support.FULL),
    CapabilityInfo(Capability.TEXT, Support.FULL, note="Caption, hashtags and mentions."),
    CapabilityInfo(
        Capability.METADATA,
        Support.PARTIAL,
        note="Like and comment counts are not always public.",
    ),
    CapabilityInfo(Capability.CAROUSEL_ZIP, Support.FULL),
)

_VIDEO_CAPABILITIES: tuple[CapabilityInfo, ...] = (
    CapabilityInfo(Capability.VIDEO, Support.FULL),
    CapabilityInfo(Capability.AUDIO, Support.FULL),
    CapabilityInfo(Capability.THUMBNAIL, Support.FULL),
    CapabilityInfo(Capability.TEXT, Support.FULL, note="Caption, hashtags and mentions."),
    CapabilityInfo(
        Capability.METADATA,
        Support.PARTIAL,
        note="View and like counts are not always public.",
    ),
    CapabilityInfo(Capability.FRAME_GRAB, Support.FULL),
)

SPEC = PlatformSpec(
    platform=Platform.INSTAGRAM,
    display_name="Instagram",
    hosts=frozenset({"instagram.com", "instagr.am", "ddinstagram.com"}),
    url_patterns=URL_PATTERNS,
    capabilities={
        ContentType.REEL: _VIDEO_CAPABILITIES,
        ContentType.VIDEO: _VIDEO_CAPABILITIES,
        ContentType.POST: _POST_CAPABILITIES,
        ContentType.CAROUSEL: _POST_CAPABILITIES,
        ContentType.STORY: (
            CapabilityInfo(
                Capability.VIDEO,
                Support.BEST_EFFORT,
                note="Stories usually require a signed-in session, which we do not provide.",
            ),
            CapabilityInfo(
                Capability.THUMBNAIL,
                Support.BEST_EFFORT,
                note="Stories usually require a signed-in session, which we do not provide.",
            ),
        ),
    },
    implemented=False,
    notes="Public reels, posts and carousels.",
    known_limitations=(
        "Private accounts are rejected -- public content only.",
        "Stories are best-effort and usually fail without a session.",
    ),
)
