"""Vocabulary for the platform capability registry.

The types here are the contract between the registry, the API and the
frontend. A capability that is not in this file does not exist as far as the
UI is concerned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class Platform(StrEnum):
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    LINKEDIN = "linkedin"
    SNAPCHAT = "snapchat"


class ContentType(StrEnum):
    VIDEO = "video"
    SHORT = "short"
    REEL = "reel"
    POST = "post"
    CAROUSEL = "carousel"
    STORY = "story"
    SPOTLIGHT = "spotlight"
    PLAYLIST = "playlist"


class Capability(StrEnum):
    """One per tab in the UI (section 8)."""

    VIDEO = "video"
    AUDIO = "audio"
    THUMBNAIL = "thumbnail"
    TEXT = "text"
    SUBTITLES = "subtitles"
    METADATA = "metadata"
    CAROUSEL_ZIP = "carousel_zip"
    FRAME_GRAB = "frame_grab"


class Support(StrEnum):
    """How much to promise the user.

    The distinction matters: `BEST_EFFORT` and `FRAGILE` are rendered with a
    warning in the UI, because section 4 forbids presenting a capability that
    often fails as though it were reliable.
    """

    FULL = "full"
    PARTIAL = "partial"
    BEST_EFFORT = "best_effort"
    FRAGILE = "fragile"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class CapabilityInfo:
    capability: Capability
    support: Support
    # Shown in the UI next to anything not FULL. Required for the degraded
    # levels so no capability is silently unreliable.
    note: str | None = None

    def __post_init__(self) -> None:
        if self.support in (Support.BEST_EFFORT, Support.FRAGILE) and not self.note:
            raise ValueError(
                f"{self.capability} is {self.support} and must carry a note explaining why"
            )


@dataclass(frozen=True)
class URLPattern:
    """Maps a URL shape onto a content type."""

    pattern: re.Pattern[str]
    content_type: ContentType
    # Named group holding the platform's own id for the content, when the URL
    # exposes one. Used for logging and dedup, never for reconstructing a URL.
    id_group: str | None = "id"


@dataclass(frozen=True)
class PlatformSpec:
    platform: Platform
    display_name: str
    # Hostnames that route to this platform, without a leading "www.".
    hosts: frozenset[str]
    url_patterns: tuple[URLPattern, ...]
    # Capabilities per content type. A content type absent from this mapping is
    # recognised but not actionable.
    capabilities: dict[ContentType, tuple[CapabilityInfo, ...]]
    # False until the platform's phase lands. Detected (so errors can be
    # specific) but rejected by /api/analyze.
    implemented: bool = False
    # Platforms that ship switched off are omitted from the registry entirely
    # rather than shown disabled (decision D-004).
    default_enabled: bool = True
    notes: str = ""
    known_limitations: tuple[str, ...] = field(default_factory=tuple)

    def match(self, url: str) -> tuple[ContentType, str | None] | None:
        """Return the content type and platform id for a URL, or None."""
        for candidate in self.url_patterns:
            found = candidate.pattern.search(url)
            if found is None:
                continue
            content_id: str | None = None
            if candidate.id_group:
                try:
                    content_id = found.group(candidate.id_group)
                except IndexError:
                    # Pattern has no such group; the content type still stands.
                    content_id = None
            return candidate.content_type, content_id
        return None

    def capabilities_for(self, content_type: ContentType) -> tuple[CapabilityInfo, ...]:
        return self.capabilities.get(content_type, ())
