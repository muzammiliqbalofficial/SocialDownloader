"""Facebook. Detected from Phase 2; implemented in Phase 5."""

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

URL_PATTERNS: tuple[URLPattern, ...] = (
    URLPattern(re.compile(r"/reel/(?P<id>\d+)"), ContentType.REEL),
    URLPattern(re.compile(r"/videos/(?:[^/]+/)?(?P<id>\d+)"), ContentType.VIDEO),
    URLPattern(re.compile(r"/watch/?\?v=(?P<id>\d+)"), ContentType.VIDEO),
    URLPattern(re.compile(r"fb\.watch/(?P<id>[A-Za-z0-9_-]+)"), ContentType.VIDEO),
    URLPattern(re.compile(r"/posts/(?P<id>[A-Za-z0-9]+)"), ContentType.POST),
)

_VIDEO_CAPABILITIES: tuple[CapabilityInfo, ...] = (
    CapabilityInfo(Capability.VIDEO, Support.FULL),
    CapabilityInfo(Capability.AUDIO, Support.FULL),
    CapabilityInfo(Capability.THUMBNAIL, Support.FULL),
    CapabilityInfo(Capability.TEXT, Support.FULL, note="Post text accompanying the video."),
    CapabilityInfo(
        Capability.METADATA,
        Support.PARTIAL,
        note="Reaction and share counts are not reliably exposed.",
    ),
    CapabilityInfo(Capability.FRAME_GRAB, Support.FULL),
)

SPEC = PlatformSpec(
    platform=Platform.FACEBOOK,
    display_name="Facebook",
    hosts=frozenset({"facebook.com", "m.facebook.com", "fb.watch", "fb.com"}),
    url_patterns=URL_PATTERNS,
    capabilities={
        ContentType.VIDEO: _VIDEO_CAPABILITIES,
        ContentType.REEL: _VIDEO_CAPABILITIES,
        ContentType.POST: (
            CapabilityInfo(Capability.TEXT, Support.PARTIAL, note="Public post text only."),
            CapabilityInfo(Capability.THUMBNAIL, Support.PARTIAL),
            CapabilityInfo(
                Capability.METADATA,
                Support.PARTIAL,
                note="Reaction and share counts are not reliably exposed.",
            ),
        ),
    },
    implemented=False,
    notes="Public videos and reels only.",
    known_limitations=(
        "Content limited to friends or groups is rejected -- public only.",
        "Facebook changes its page structure frequently; expect periodic breakage.",
    ),
)
