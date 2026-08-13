"""Snapchat. Detected from Phase 2; implemented in Phase 5, shipped disabled.

Per decision D-004 this platform defaults to off. When disabled it is omitted
from the registry entirely -- the UI must not show a greyed-out or failing tab.
Enable with SNAPCHAT_ENABLED=true once Spotlight extraction is proven.
"""

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
    URLPattern(re.compile(r"/spotlight/(?P<id>[A-Za-z0-9_-]+)"), ContentType.SPOTLIGHT),
    URLPattern(re.compile(r"/add/(?P<id>[A-Za-z0-9_.-]+)"), ContentType.STORY),
    URLPattern(re.compile(r"/t/(?P<id>[A-Za-z0-9_-]+)"), ContentType.SPOTLIGHT),
)

SPEC = PlatformSpec(
    platform=Platform.SNAPCHAT,
    display_name="Snapchat",
    hosts=frozenset({"snapchat.com", "story.snapchat.com"}),
    url_patterns=URL_PATTERNS,
    capabilities={
        ContentType.SPOTLIGHT: (
            CapabilityInfo(
                Capability.VIDEO,
                Support.BEST_EFFORT,
                note="Spotlight only. Snapchat rotates its player frequently.",
            ),
            CapabilityInfo(
                Capability.AUDIO,
                Support.BEST_EFFORT,
                note="Derived from the Spotlight video, when that succeeds.",
            ),
            CapabilityInfo(
                Capability.THUMBNAIL,
                Support.BEST_EFFORT,
                note="Snapchat rotates its player frequently.",
            ),
            CapabilityInfo(
                Capability.METADATA,
                Support.PARTIAL,
                note="Minimal: little beyond a title and the creator handle.",
            ),
        ),
    },
    implemented=False,
    default_enabled=False,
    notes="Spotlight only. Stories are not reliably accessible.",
    known_limitations=(
        "Disabled by default; enable with SNAPCHAT_ENABLED=true.",
        "Stories are not supported at all -- they are not reliably accessible.",
        "Metadata is minimal compared with other platforms.",
    ),
)
