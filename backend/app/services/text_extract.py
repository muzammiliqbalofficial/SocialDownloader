"""Parse hashtags and mentions out of post text (Tier 2).

Kept separate from any extractor so the same rules apply to a YouTube
description, an Instagram caption and a LinkedIn post body.
"""

from __future__ import annotations

import re

from app.models.extraction import ExtractedText

# \w is Unicode-aware by default in Python 3, so accented characters and
# non-Latin scripts work without listing ranges. The lookbehind rejects "&#39;"
# and URL fragments, which would otherwise read as hashtags.
_HASHTAG = re.compile(r"(?<![\w&/])#(\w+)")
# Mentions may contain dots and hyphens internally but must not end with one.
_MENTION = re.compile(r"(?<![\w/])@(\w[\w.-]*)")


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    """Case-insensitive dedup, keeping the first spelling the author used."""
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            ordered.append(value)
    return ordered


def extract_text(body: str | None) -> ExtractedText:
    if not body:
        return ExtractedText(body=None, character_count=0)

    hashtags = _dedupe_preserving_order(_HASHTAG.findall(body))
    mentions = _dedupe_preserving_order(
        # Strip trailing punctuation so "@someone." yields "someone".
        [stripped for m in _MENTION.findall(body) if (stripped := m.rstrip(".-"))]
    )

    return ExtractedText(
        body=body,
        # len() over the original string: line breaks and emoji are preserved
        # and counted as the author sees them.
        character_count=len(body),
        hashtags=hashtags,
        mentions=mentions,
    )
