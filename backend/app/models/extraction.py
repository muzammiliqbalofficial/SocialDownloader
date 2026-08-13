"""Normalised extraction shapes.

Deliberately independent of yt-dlp's dictionary format. Everything outside
`app/extractors/` speaks these types, so replacing the extraction engine is a
change to one module rather than a rewrite (section 6).
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class StreamKind(StrEnum):
    AUDIO_VIDEO = "audio_video"
    VIDEO_ONLY = "video_only"
    AUDIO_ONLY = "audio_only"


class MediaFormat(BaseModel):
    format_id: str
    kind: StreamKind
    ext: str
    # Human label for the picker: "1080p", "720p60", "128kbps".
    quality_label: str
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    vcodec: str | None = None
    acodec: str | None = None
    # Exact where the platform reports it, estimated otherwise. The distinction
    # matters in the UI: an estimate should not be shown as a precise number.
    filesize_bytes: int | None = None
    filesize_is_estimate: bool = False
    total_bitrate_kbps: float | None = None
    audio_bitrate_kbps: float | None = None
    dynamic_range: str | None = None
    protocol: str | None = None


class Thumbnail(BaseModel):
    url: str
    width: int | None = None
    height: int | None = None
    label: str | None = None

    @property
    def pixels(self) -> int:
        return (self.width or 0) * (self.height or 0)


class SubtitleTrack(BaseModel):
    language: str
    language_name: str | None = None
    auto_generated: bool = False
    formats: list[str] = Field(default_factory=list)


class Chapter(BaseModel):
    title: str
    start_seconds: float
    end_seconds: float | None = None


class ExtractedText(BaseModel):
    """Post text plus what can be parsed out of it (Tier 2)."""

    body: str | None = None
    character_count: int = 0
    hashtags: list[str] = Field(default_factory=list)
    mentions: list[str] = Field(default_factory=list)


class MediaMetadata(BaseModel):
    title: str | None = None
    author: str | None = None
    author_url: str | None = None
    upload_date: date | None = None
    duration_seconds: float | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    is_live: bool = False
    age_limit: int = 0
    width: int | None = None
    height: int | None = None


class ExtractionResult(BaseModel):
    """What the extractor layer hands back for a single piece of content."""

    content_id: str | None = None
    metadata: MediaMetadata = Field(default_factory=MediaMetadata)
    text: ExtractedText = Field(default_factory=ExtractedText)
    formats: list[MediaFormat] = Field(default_factory=list)
    thumbnails: list[Thumbnail] = Field(default_factory=list)
    subtitles: list[SubtitleTrack] = Field(default_factory=list)
    chapters: list[Chapter] = Field(default_factory=list)
    # True when any stream is DRM-protected. The API refuses these outright
    # (constraint 2); it never reaches a download path.
    has_drm: bool = False
