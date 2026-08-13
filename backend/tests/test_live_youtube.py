"""Live canary against real YouTube URLs.

Excluded from the default suite and from CI. Run with `pytest -m live`.
The weekly yt-dlp canary workflow runs these; **they are expected to fail
periodically**, and when they do it means extraction is broken in production,
not that the test is wrong.

Assertions are deliberately loose about values and strict about shape: view
counts change, the presence of a 1080p format does not.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.extractors import ytdlp_adapter
from app.models.extraction import StreamKind

pytestmark = [pytest.mark.live, pytest.mark.anyio]

# Blender Foundation's Big Buck Bunny: Creative Commons, stable for a decade,
# not going to be taken down or age-gated.
STABLE_VIDEO = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


@pytest.fixture
def timeout() -> int:
    return get_settings().extractor_timeout_seconds


async def test_extracts_a_real_video(timeout):
    result = await ytdlp_adapter.extract(STABLE_VIDEO, timeout=timeout)

    assert result.content_id == "aqz-KE-bpKQ"
    assert result.metadata.title
    assert result.metadata.author
    assert result.metadata.duration_seconds and result.metadata.duration_seconds > 0
    assert result.metadata.upload_date is not None
    assert result.has_drm is False


async def test_real_video_offers_both_video_and_audio_formats(timeout):
    result = await ytdlp_adapter.extract(STABLE_VIDEO, timeout=timeout)

    kinds = {f.kind for f in result.formats}
    assert StreamKind.AUDIO_ONLY in kinds
    assert kinds & {StreamKind.VIDEO_ONLY, StreamKind.AUDIO_VIDEO}
    assert all(f.format_id and f.ext and f.quality_label for f in result.formats)


async def test_real_video_exposes_thumbnails_and_subtitles(timeout):
    result = await ytdlp_adapter.extract(STABLE_VIDEO, timeout=timeout)

    assert result.thumbnails
    assert result.thumbnails[0].pixels >= result.thumbnails[-1].pixels
    # YouTube auto-generates captions for essentially everything.
    assert result.subtitles


async def test_a_removed_video_maps_to_a_specific_error(timeout):
    """Canary for the error mapping, not just the happy path: if this starts
    returning EXTRACTOR_OUTDATED, yt-dlp changed its wording."""
    from app.core.errors import AppError, ErrorCode

    with pytest.raises(AppError) as excinfo:
        await ytdlp_adapter.extract(
            "https://www.youtube.com/watch?v=aaaaaaaaaaa", timeout=timeout
        )

    assert excinfo.value.code in (
        ErrorCode.CONTENT_REMOVED,
        ErrorCode.PRIVATE_CONTENT,
        ErrorCode.UNSUPPORTED_CONTENT_TYPE,
    )
