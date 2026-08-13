"""LinkedIn. Detected from Phase 2; implemented in Phase 5.

Flagged fragile throughout: there is no public extractor for LinkedIn posts, so
text comes from `og:` meta tags first and a Playwright render only as a
fallback. Both break when LinkedIn changes its markup.
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

_FRAGILE_NOTE = (
    "LinkedIn exposes no public API for this; "
    "extraction breaks when their markup changes."
)

URL_PATTERNS: tuple[URLPattern, ...] = (
    URLPattern(re.compile(r"/posts/(?P<id>[A-Za-z0-9_%-]+)"), ContentType.POST),
    URLPattern(re.compile(r"/feed/update/(?P<id>[A-Za-z0-9:_-]+)"), ContentType.POST),
    URLPattern(re.compile(r"/video/(?:live/)?(?P<id>[A-Za-z0-9_-]+)"), ContentType.VIDEO),
)

_POST_CAPABILITIES: tuple[CapabilityInfo, ...] = (
    CapabilityInfo(
        Capability.TEXT,
        Support.FRAGILE,
        note=f"Full post text. {_FRAGILE_NOTE}",
    ),
    CapabilityInfo(Capability.THUMBNAIL, Support.FRAGILE, note=_FRAGILE_NOTE),
    CapabilityInfo(
        Capability.METADATA,
        Support.PARTIAL,
        note="Author and date only; engagement counts are not exposed.",
    ),
)

SPEC = PlatformSpec(
    platform=Platform.LINKEDIN,
    display_name="LinkedIn",
    hosts=frozenset({"linkedin.com", "lnkd.in"}),
    url_patterns=URL_PATTERNS,
    capabilities={
        ContentType.POST: _POST_CAPABILITIES,
        ContentType.VIDEO: (
            CapabilityInfo(
                Capability.VIDEO,
                Support.FRAGILE,
                note=f"Native video only. {_FRAGILE_NOTE}",
            ),
            CapabilityInfo(Capability.AUDIO, Support.FRAGILE, note=_FRAGILE_NOTE),
            CapabilityInfo(Capability.THUMBNAIL, Support.FRAGILE, note=_FRAGILE_NOTE),
            CapabilityInfo(
                Capability.TEXT,
                Support.FRAGILE,
                note=f"Full post text. {_FRAGILE_NOTE}",
            ),
            CapabilityInfo(
                Capability.METADATA,
                Support.PARTIAL,
                note="Author and date only; engagement counts are not exposed.",
            ),
            CapabilityInfo(Capability.FRAME_GRAB, Support.FRAGILE, note=_FRAGILE_NOTE),
        ),
    },
    implemented=False,
    notes="Native video only; embedded YouTube links are not unwrapped.",
    known_limitations=(
        "Everything here is fragile -- LinkedIn has no public extractor.",
        "Posts requiring a login are rejected.",
        "Documents and carousels posted as PDFs are not supported.",
    ),
)
