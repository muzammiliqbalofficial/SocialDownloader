"""YouTube: the only platform implemented as of Phase 2."""

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

# youtu.be/<id>, /watch?v=<id>, /shorts/<id>, /embed/<id>, /live/<id>.
# Video ids are exactly 11 chars of [A-Za-z0-9_-].
_VIDEO_ID = r"(?P<id>[A-Za-z0-9_-]{11})"

URL_PATTERNS: tuple[URLPattern, ...] = (
    URLPattern(re.compile(rf"youtu\.be/{_VIDEO_ID}"), ContentType.VIDEO),
    URLPattern(re.compile(rf"/shorts/{_VIDEO_ID}"), ContentType.SHORT),
    URLPattern(re.compile(rf"/live/{_VIDEO_ID}"), ContentType.VIDEO),
    URLPattern(re.compile(rf"/embed/{_VIDEO_ID}"), ContentType.VIDEO),
    URLPattern(re.compile(rf"[?&]v={_VIDEO_ID}"), ContentType.VIDEO),
    URLPattern(re.compile(r"[?&]list=(?P<id>[A-Za-z0-9_-]+)"), ContentType.PLAYLIST),
)

_FULL_VIDEO_CAPABILITIES: tuple[CapabilityInfo, ...] = (
    CapabilityInfo(Capability.VIDEO, Support.FULL),
    CapabilityInfo(Capability.AUDIO, Support.FULL),
    CapabilityInfo(Capability.THUMBNAIL, Support.FULL),
    CapabilityInfo(
        Capability.TEXT,
        Support.FULL,
        note="Description and chapters. YouTube has no post caption as such.",
    ),
    CapabilityInfo(Capability.SUBTITLES, Support.FULL),
    CapabilityInfo(Capability.METADATA, Support.FULL),
    CapabilityInfo(Capability.FRAME_GRAB, Support.FULL),
)

SPEC = PlatformSpec(
    platform=Platform.YOUTUBE,
    display_name="YouTube",
    hosts=frozenset(
        {
            "youtube.com",
            "m.youtube.com",
            "music.youtube.com",
            "youtube-nocookie.com",
            "youtu.be",
        }
    ),
    url_patterns=URL_PATTERNS,
    capabilities={
        ContentType.VIDEO: _FULL_VIDEO_CAPABILITIES,
        ContentType.SHORT: _FULL_VIDEO_CAPABILITIES,
    },
    implemented=True,
    notes="Includes Shorts. Subtitles include auto-generated tracks.",
    known_limitations=(
        "Playlists are recognised but not downloadable; paste a single video URL.",
        "Age-restricted videos require a signed-in session and are rejected.",
        "Live streams still in progress are not supported.",
    ),
)
