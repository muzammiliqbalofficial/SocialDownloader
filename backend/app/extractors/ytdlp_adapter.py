"""The only module that knows yt-dlp exists.

Everything else speaks `app.models.extraction` types. When yt-dlp is eventually
replaced -- and it will be, it is the most fragile dependency in the project --
this file is the blast radius.

Two deliberate choices:

* **Subprocess, not the Python API.** yt-dlp can hang on a stalled socket or
  wedge inside an extractor. A child process can be killed; a thread running
  library code cannot. The subprocess also gives us a hard timeout that
  actually holds, and keeps yt-dlp's global state out of our process.

* **Cookies are never written to disk** (constraint 3). yt-dlp only accepts a
  cookie *file*, so when Phase 5 needs one the cookie jar must be passed
  through an anonymous pipe as `/dev/fd/N`, never a temp file. Do not
  "temporarily" write one out.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from datetime import date, datetime
from typing import Any

from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger
from app.extractors.errors_map import is_recognised, map_stderr
from app.models.extraction import (
    Chapter,
    ExtractionResult,
    MediaFormat,
    MediaMetadata,
    StreamKind,
    SubtitleTrack,
    Thumbnail,
)
from app.services.text_extract import extract_text

_log = get_logger("extractor.ytdlp")

# Cap on how much stderr we retain for classification. yt-dlp can emit a great
# deal of it; we only need enough to match a rule and log a tail.
_MAX_STDERR_BYTES = 64_000
# --dump-single-json on a long playlist can be enormous. Analyze is
# single-item, so anything past this is pathological.
_MAX_STDOUT_BYTES = 32 * 1024 * 1024


def _base_command(cache_dir: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "yt_dlp",
        # Never read a user or system config: extraction must be deterministic
        # and must not pick up flags we did not choose.
        "--ignore-config",
        "--no-playlist",
        "--no-progress",
        "--no-color",
        # Cloud Run gives us a writable /tmp and nothing else.
        "--cache-dir",
        cache_dir,
        "--socket-timeout",
        "15",
        "--retries",
        "2",
        "--extractor-retries",
        "1",
    ]


async def _run(args: list[str], *, timeout: int) -> tuple[int, bytes, bytes]:
    """Run yt-dlp, enforcing the timeout by killing the whole process group.

    yt-dlp spawns ffmpeg and other children; killing only the parent would
    leave those running and holding the CPU.
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        # New session so the kill below reaches descendants too.
        start_new_session=True,
    )

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        _kill_process_group(process)
        # Reap the child so it does not linger as a zombie.
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:  # pragma: no cover - the kill above should suffice
            pass
        raise AppError(
            ErrorCode.UPSTREAM_TIMEOUT,
            detail=f"The platform did not respond within {timeout} seconds.",
        ) from None

    return process.returncode or 0, stdout, stderr


def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(os.getpgid(process.pid), 9)
    except (ProcessLookupError, PermissionError):  # pragma: no cover - already gone
        try:
            process.kill()
        except ProcessLookupError:
            pass


async def fetch_info(url: str, *, timeout: int) -> dict[str, Any]:
    """Return yt-dlp's raw info dict for a single item.

    Raises AppError mapped into the taxonomy on any failure.
    """
    cache_dir = os.path.join(os.getenv("TMP_DIR", "/tmp/socialdl"), "yt-dlp-cache")
    os.makedirs(cache_dir, exist_ok=True)

    args = [*_base_command(cache_dir), "--dump-single-json", "--skip-download", "--", url]

    returncode, stdout, stderr = await _run(args, timeout=timeout)
    stderr_text = stderr[:_MAX_STDERR_BYTES].decode("utf-8", errors="replace")

    if returncode != 0:
        code = map_stderr(stderr_text)
        # Log the classification, never the URL (section 6). The stderr tail is
        # kept short and is platform output, not user input.
        _log.warning(
            "extractor.failed",
            error_code=str(code),
            recognised=is_recognised(stderr_text),
            returncode=returncode,
            stderr_tail=stderr_text.strip()[-400:],
        )
        raise AppError(code, context={"returncode": returncode})

    if len(stdout) > _MAX_STDOUT_BYTES:
        raise AppError(
            ErrorCode.FILESIZE_EXCEEDED,
            detail="That link expands to far more content than we can analyse at once.",
        )

    try:
        info = json.loads(stdout)
    except json.JSONDecodeError as exc:
        _log.error("extractor.bad_json", stderr_tail=stderr_text.strip()[-400:])
        raise AppError(ErrorCode.EXTRACTOR_OUTDATED, cause=exc) from exc

    if not isinstance(info, dict):
        raise AppError(ErrorCode.EXTRACTOR_OUTDATED, detail="Unexpected extractor output.")

    return info


# --------------------------------------------------------------------------
# Normalisation: yt-dlp's dict -> our types
# --------------------------------------------------------------------------


def _stream_kind(fmt: dict[str, Any]) -> StreamKind:
    vcodec = (fmt.get("vcodec") or "none").lower()
    acodec = (fmt.get("acodec") or "none").lower()
    has_video = vcodec not in ("none", "")
    has_audio = acodec not in ("none", "")
    if has_video and has_audio:
        return StreamKind.AUDIO_VIDEO
    if has_video:
        return StreamKind.VIDEO_ONLY
    return StreamKind.AUDIO_ONLY


def _quality_label(fmt: dict[str, Any], kind: StreamKind) -> str:
    if kind is StreamKind.AUDIO_ONLY:
        abr = fmt.get("abr")
        if abr:
            return f"{round(abr)}kbps"
        return fmt.get("format_note") or fmt.get("ext") or "audio"

    height = fmt.get("height")
    if height:
        fps = fmt.get("fps")
        # 60fps is worth surfacing; 30 and below is the unremarkable default.
        return f"{height}p{round(fps)}" if fps and fps >= 50 else f"{height}p"
    return fmt.get("format_note") or fmt.get("resolution") or "video"


def _normalise_formats(info: dict[str, Any]) -> tuple[list[MediaFormat], bool]:
    formats: list[MediaFormat] = []
    has_drm = bool(info.get("_has_drm"))

    for raw in info.get("formats") or []:
        if not isinstance(raw, dict):
            continue
        if raw.get("has_drm"):
            has_drm = True
            # Never offer a DRM-protected stream as a choice.
            continue
        # Storyboard/preview pseudo-formats are not downloadable media.
        if (raw.get("format_note") or "").lower() == "storyboard":
            continue
        if (raw.get("vcodec") or "none") == "none" and (raw.get("acodec") or "none") == "none":
            continue

        format_id = raw.get("format_id")
        if not format_id:
            continue

        kind = _stream_kind(raw)
        filesize = raw.get("filesize")
        approx = raw.get("filesize_approx")

        formats.append(
            MediaFormat(
                format_id=str(format_id),
                kind=kind,
                ext=raw.get("ext") or "bin",
                quality_label=_quality_label(raw, kind),
                width=raw.get("width"),
                height=raw.get("height"),
                fps=raw.get("fps"),
                vcodec=None if (raw.get("vcodec") in (None, "none")) else raw.get("vcodec"),
                acodec=None if (raw.get("acodec") in (None, "none")) else raw.get("acodec"),
                filesize_bytes=filesize or approx,
                filesize_is_estimate=filesize is None and approx is not None,
                total_bitrate_kbps=raw.get("tbr"),
                audio_bitrate_kbps=raw.get("abr"),
                dynamic_range=raw.get("dynamic_range"),
                protocol=raw.get("protocol"),
            )
        )

    # Best first: video by pixel count then bitrate, audio by bitrate.
    formats.sort(
        key=lambda f: (
            (f.height or 0) * (f.width or 0),
            f.total_bitrate_kbps or f.audio_bitrate_kbps or 0,
        ),
        reverse=True,
    )
    return formats, has_drm


def _normalise_thumbnails(info: dict[str, Any]) -> list[Thumbnail]:
    thumbnails: list[Thumbnail] = []
    seen: set[str] = set()

    for raw in info.get("thumbnails") or []:
        if not isinstance(raw, dict):
            continue
        url = raw.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        thumbnails.append(
            Thumbnail(
                url=url,
                width=raw.get("width"),
                height=raw.get("height"),
                label=str(raw["id"]) if raw.get("id") is not None else None,
            )
        )

    # Fall back to the single `thumbnail` key when the list is absent.
    single = info.get("thumbnail")
    if not thumbnails and single:
        thumbnails.append(Thumbnail(url=single))

    thumbnails.sort(key=lambda t: t.pixels, reverse=True)
    return thumbnails


def _normalise_subtitles(info: dict[str, Any]) -> list[SubtitleTrack]:
    tracks: list[SubtitleTrack] = []

    for source, auto in (("subtitles", False), ("automatic_captions", True)):
        for language, entries in (info.get(source) or {}).items():
            if not isinstance(entries, list) or not entries:
                continue
            extensions = sorted(
                {e.get("ext") for e in entries if isinstance(e, dict) and e.get("ext")}
            )
            name = next(
                (e.get("name") for e in entries if isinstance(e, dict) and e.get("name")),
                None,
            )
            tracks.append(
                SubtitleTrack(
                    language=language,
                    language_name=name,
                    auto_generated=auto,
                    formats=extensions,
                )
            )

    # Manual tracks before auto-generated, then alphabetical.
    tracks.sort(key=lambda t: (t.auto_generated, t.language))
    return tracks


def _parse_upload_date(info: dict[str, Any]) -> date | None:
    raw = info.get("upload_date")
    if raw:
        try:
            return datetime.strptime(str(raw), "%Y%m%d").date()
        except ValueError:
            pass
    timestamp = info.get("timestamp")
    if isinstance(timestamp, int | float):
        try:
            return datetime.fromtimestamp(timestamp).date()
        except (OverflowError, OSError, ValueError):  # pragma: no cover - defensive
            return None
    return None


def _normalise_chapters(info: dict[str, Any]) -> list[Chapter]:
    chapters: list[Chapter] = []
    for raw in info.get("chapters") or []:
        if not isinstance(raw, dict):
            continue
        start = raw.get("start_time")
        if start is None:
            continue
        chapters.append(
            Chapter(
                title=raw.get("title") or "Untitled chapter",
                start_seconds=float(start),
                end_seconds=float(raw["end_time"]) if raw.get("end_time") is not None else None,
            )
        )
    return chapters


def normalise(info: dict[str, Any]) -> ExtractionResult:
    """Convert a yt-dlp info dict into our own types."""
    formats, has_drm = _normalise_formats(info)

    live_status = info.get("live_status")
    is_live = bool(info.get("is_live")) or live_status in ("is_live", "post_live")

    description = info.get("description")

    metadata = MediaMetadata(
        title=info.get("title"),
        author=info.get("uploader") or info.get("channel") or info.get("creator"),
        author_url=info.get("uploader_url") or info.get("channel_url"),
        upload_date=_parse_upload_date(info),
        duration_seconds=info.get("duration"),
        view_count=info.get("view_count"),
        like_count=info.get("like_count"),
        comment_count=info.get("comment_count"),
        is_live=is_live,
        age_limit=info.get("age_limit") or 0,
        width=info.get("width"),
        height=info.get("height"),
    )

    return ExtractionResult(
        content_id=info.get("id"),
        metadata=metadata,
        text=extract_text(description),
        formats=formats,
        thumbnails=_normalise_thumbnails(info),
        subtitles=_normalise_subtitles(info),
        chapters=_normalise_chapters(info),
        has_drm=has_drm,
    )


async def extract(url: str, *, timeout: int) -> ExtractionResult:
    """Fetch and normalise metadata for a single item. No media is downloaded."""
    info = await fetch_info(url, timeout=timeout)
    result = normalise(info)

    # Constraint 2: refuse DRM outright, before anything downstream can try.
    if result.has_drm:
        raise AppError(ErrorCode.DRM_PROTECTED)

    return result


def ytdlp_version() -> str | None:
    """Reported on the health endpoint so a stale pin is visible in production."""
    try:
        from yt_dlp.version import __version__

        return __version__
    except Exception:  # pragma: no cover - only if the dependency is broken
        return None


def ffmpeg_available() -> bool:
    """Merging separate video and audio streams needs ffmpeg; without it the
    best formats silently become unavailable."""
    return shutil.which("ffmpeg") is not None
