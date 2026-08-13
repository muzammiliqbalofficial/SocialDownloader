"""Normalising yt-dlp's info dict into our own types, from committed fixtures
so the suite runs offline (section 12)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.core.errors import AppError, ErrorCode
from app.extractors.ytdlp_adapter import normalise
from app.models.extraction import StreamKind

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def info() -> dict:
    return json.loads((FIXTURES / "youtube_video.json").read_text(encoding="utf-8"))


@pytest.fixture
def result(info):
    return normalise(info)


def test_metadata_is_mapped(result):
    meta = result.metadata
    assert meta.title.startswith("Big Buck Bunny")
    assert meta.author == "Blender Foundation"
    assert meta.upload_date == date(2014, 11, 10)
    assert meta.duration_seconds == 635
    assert meta.view_count == 12345678
    assert meta.like_count == 98765
    assert meta.comment_count == 4321
    assert meta.is_live is False
    assert meta.age_limit == 0


def test_storyboard_pseudo_formats_are_dropped(result):
    """Storyboards are preview sprites, not downloadable media, and would be
    nonsense in the format picker."""
    assert all(f.format_id != "sb0" for f in result.formats)


def test_formats_are_classified_by_stream_kind(result):
    by_id = {f.format_id: f for f in result.formats}
    assert by_id["313"].kind is StreamKind.VIDEO_ONLY
    assert by_id["140"].kind is StreamKind.AUDIO_ONLY
    assert by_id["18"].kind is StreamKind.AUDIO_VIDEO


def test_quality_labels_are_human_readable(result):
    by_id = {f.format_id: f for f in result.formats}
    assert by_id["313"].quality_label == "2160p60"
    assert by_id["137"].quality_label == "1080p60"
    assert by_id["18"].quality_label == "360p"
    assert by_id["140"].quality_label == "128kbps"


def test_estimated_filesizes_are_flagged_as_estimates(result):
    """The UI must not present an approximation as an exact number."""
    by_id = {f.format_id: f for f in result.formats}
    assert by_id["137"].filesize_bytes == 356789012
    assert by_id["137"].filesize_is_estimate is False
    assert by_id["313"].filesize_bytes == 1500000000
    assert by_id["313"].filesize_is_estimate is True


def test_best_format_sorts_first(result):
    assert result.formats[0].format_id == "313"


def test_thumbnails_are_sorted_largest_first_including_maxres(result):
    assert result.thumbnails[0].width == 1920
    assert result.thumbnails[0].label == "maxresdefault"
    assert len(result.thumbnails) == 5
    widths = [t.width for t in result.thumbnails]
    assert widths == sorted(widths, reverse=True)


def test_subtitles_separate_manual_from_auto_generated(result):
    manual = [t for t in result.subtitles if not t.auto_generated]
    auto = [t for t in result.subtitles if t.auto_generated]
    assert {t.language for t in manual} == {"en", "nl"}
    assert {t.language for t in auto} == {"en", "de"}
    # Manual tracks first: they are the better choice when both exist.
    assert result.subtitles[0].auto_generated is False


def test_subtitle_formats_are_listed(result):
    english = next(t for t in result.subtitles if t.language == "en" and not t.auto_generated)
    assert set(english.formats) == {"vtt", "srv3"}


def test_chapters_are_mapped(result):
    assert len(result.chapters) == 3
    assert result.chapters[0].title == "Intro"
    assert result.chapters[0].start_seconds == 0.0
    assert result.chapters[2].end_seconds == 635.0


def test_description_is_parsed_for_hashtags_and_mentions(result):
    assert result.text.hashtags == ["Blender", "OpenMovie", "b3d"]
    assert result.text.mentions == ["BlenderFoundation", "peach.project"]
    assert result.text.character_count == len(result.text.body)


def test_drm_formats_are_never_offered():
    info = json.loads((FIXTURES / "youtube_drm.json").read_text(encoding="utf-8"))
    result = normalise(info)
    assert result.has_drm is True
    assert result.formats == []


async def test_extract_refuses_drm_outright(monkeypatch):
    """Constraint 2: fail immediately, never hand a DRM stream downstream."""
    from app.extractors import ytdlp_adapter

    info = json.loads((FIXTURES / "youtube_drm.json").read_text(encoding="utf-8"))

    async def fake_fetch(url: str, *, timeout: int) -> dict:
        return info

    monkeypatch.setattr(ytdlp_adapter, "fetch_info", fake_fetch)

    with pytest.raises(AppError) as excinfo:
        await ytdlp_adapter.extract("https://youtu.be/drmExample1", timeout=10)
    assert excinfo.value.code is ErrorCode.DRM_PROTECTED


def test_normalise_survives_a_sparse_info_dict():
    """yt-dlp omits keys freely depending on extractor and content."""
    result = normalise({"id": "x"})
    assert result.content_id == "x"
    assert result.formats == []
    assert result.thumbnails == []
    assert result.metadata.title is None
    assert result.text.character_count == 0


def test_normalise_ignores_malformed_entries():
    result = normalise(
        {
            "id": "x",
            "formats": ["not a dict", {"no_format_id": True}, None],
            "thumbnails": ["nope", {"width": 10}],
            "chapters": [{"title": "no start time"}],
        }
    )
    assert result.formats == []
    assert result.thumbnails == []
    assert result.chapters == []


def test_upload_date_falls_back_to_timestamp():
    result = normalise({"id": "x", "timestamp": 1415577600})
    assert result.metadata.upload_date is not None


def test_live_status_is_detected():
    assert normalise({"id": "x", "live_status": "is_live"}).metadata.is_live is True
    assert normalise({"id": "x", "is_live": True}).metadata.is_live is True
    assert normalise({"id": "x", "live_status": "not_live"}).metadata.is_live is False
