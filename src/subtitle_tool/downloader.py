"""YouTube video downloader using yt-dlp with retry support."""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

from subtitle_tool.config import MAX_DURATION_SECONDS, AppConfig, get_quality_for_duration
from subtitle_tool.exceptions import DownloadError, ValidationError, retry

logger = logging.getLogger(__name__)


@dataclass
class VideoInfo:
    """Metadata for a downloaded YouTube video."""

    title: str
    duration: float  # seconds
    video_id: str
    url: str
    filepath: Path | None = None
    subtitle_path: Path | None = None


def _get_ffmpeg_dir(config: AppConfig | None) -> str | None:
    """Get the directory containing FFmpeg binary."""
    if config is None:
        return None
    ffmpeg_path = Path(config.ffmpeg_path)
    if ffmpeg_path.is_file():
        return str(ffmpeg_path.parent)
    # Check if ffmpeg is in PATH
    found = shutil.which(config.ffmpeg_path)
    if found:
        return str(Path(found).parent)
    return None


def _apply_cookies(opts: dict, config: AppConfig | None) -> None:
    """Add browser cookies config to yt-dlp options if configured."""
    if config and config.cookies_browser and config.cookies_browser != "none":
        opts["cookiesfrombrowser"] = (config.cookies_browser,)


def _is_youtube_url(url: str) -> bool:
    """Check if URL is a valid YouTube video or channel URL."""
    return bool(re.match(
        r"https?://(www\.)?(youtube\.com|youtu\.be)/", url, re.IGNORECASE
    ))


def get_video_info(url: str, config: AppConfig | None = None) -> VideoInfo:
    """Fetch video metadata without downloading."""
    if not _is_youtube_url(url):
        raise ValidationError(f"Not a valid YouTube URL: {url}")

    opts: dict = {"quiet": True, "no_warnings": True, "skip_download": True}
    ffmpeg_dir = _get_ffmpeg_dir(config)
    if ffmpeg_dir:
        opts["ffmpeg_location"] = ffmpeg_dir
    _apply_cookies(opts, config)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise DownloadError(f"Failed to fetch video info: {exc}") from exc

    if info is None:
        raise DownloadError(f"No video info returned for URL: {url}")

    return VideoInfo(
        title=info.get("title", "unknown"),
        duration=float(info.get("duration", 0)),
        video_id=info.get("id", "unknown"),
        url=url,
    )


def _sanitize_filename(title: str) -> str:
    """Remove problematic characters from video title for use as filename."""
    replacements = {'\\': '_', '/': '_', ':': '_', '*': '_', '?': '', '"': '', '<': '', '>': '', '|': '_'}
    result = title
    for char, replacement in replacements.items():
        result = result.replace(char, replacement)
    return result[:100].strip()


@retry(max_attempts=3, delay=2.0, exceptions=(DownloadError,))
def download_video(url: str, config: AppConfig) -> VideoInfo:
    """Download a YouTube video with quality based on duration.

    Raises:
        ValidationError: If video exceeds maximum duration.
        DownloadError: If download fails.
    """
    video_info = get_video_info(url, config)

    if video_info.duration > MAX_DURATION_SECONDS:
        raise ValidationError(
            f"Video '{video_info.title}' is {video_info.duration / 60:.1f} min "
            f"(max {MAX_DURATION_SECONDS / 60:.0f} min). Skipping."
        )

    target_height = get_quality_for_duration(video_info.duration)
    safe_title = _sanitize_filename(video_info.title)
    output_template = str(config.temp_dir / f"{safe_title}.%(ext)s")

    logger.info(
        "Downloading '%s' (%.1f min) at %dp...",
        video_info.title,
        video_info.duration / 60,
        target_height,
    )

    ffmpeg_dir = _get_ffmpeg_dir(config)

    opts: dict = {
        "format": f"bestvideo[height<={target_height}]+bestaudio/best[height<={target_height}]",
        "outtmpl": output_template,
        "merge_output_format": "mp4",
        "quiet": False,
        "no_warnings": False,
        # Download auto-generated subtitles in target language
        "writeautomaticsub": True,
        "subtitleslangs": [config.whisper.language if config.whisper.language != "auto" else "en"],
        "subtitlesformat": "srt",
    }

    if ffmpeg_dir:
        opts["ffmpeg_location"] = ffmpeg_dir
    _apply_cookies(opts, config)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:
        raise DownloadError(f"Download failed for '{video_info.title}': {exc}") from exc

    if info is None:
        raise DownloadError(f"Download returned no info for '{video_info.title}'")

    # Find the downloaded video file
    downloaded_path = _find_downloaded_file(config.temp_dir, safe_title, "mp4")
    if downloaded_path is None:
        raise DownloadError(f"Cannot find downloaded video file for '{video_info.title}'")

    video_info.filepath = downloaded_path

    # Check for downloaded subtitle
    sub_path = _find_downloaded_file(config.temp_dir, safe_title, "srt")
    if sub_path:
        video_info.subtitle_path = sub_path
        logger.info("Found YouTube auto-subtitle: %s", sub_path.name)

    logger.info("Downloaded: %s", downloaded_path.name)
    return video_info


def list_channel_videos(channel_url: str, max_count: int = 10, config: AppConfig | None = None) -> list[str]:
    """List video URLs from a YouTube channel (most recent first)."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "playlistend": max_count,
    }
    _apply_cookies(opts, config)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
    except Exception as exc:
        raise DownloadError(f"Failed to list channel videos: {exc}") from exc

    if info is None or "entries" not in info:
        return []

    urls: list[str] = []
    for entry in info["entries"]:
        if entry and "url" in entry:
            video_url = entry["url"]
            if not video_url.startswith("http"):
                video_url = f"https://www.youtube.com/watch?v={entry.get('id', entry['url'])}"
            urls.append(video_url)

    return urls[:max_count]


def _find_downloaded_file(directory: Path, name_prefix: str, extension: str) -> Path | None:
    """Find a file in directory matching the name prefix and extension.

    Searches by stem prefix first (exact), then by partial stem match.
    Never returns a random file — always requires a name match.
    """
    # Exact stem match
    for file in directory.iterdir():
        if file.stem == name_prefix and file.suffix == f".{extension}":
            return file

    # Stem starts with prefix (yt-dlp adds .en, .f398 etc.)
    for file in directory.iterdir():
        if file.name.startswith(name_prefix) and file.name.endswith(f".{extension}"):
            return file

    return None
