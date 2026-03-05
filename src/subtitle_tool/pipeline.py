"""Pipeline orchestrator: download → transcribe → process."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from subtitle_tool.config import AppConfig
from subtitle_tool.downloader import VideoInfo, download_video, list_channel_videos
from subtitle_tool.exceptions import SubtitleToolError, ValidationError
from subtitle_tool.processor import burn_with_filterscript, cleanup_temp_files
from subtitle_tool.quality import check_quality
from subtitle_tool.srt_utils import generate_subtitle_filterscript, normalize_srt_single_line
from subtitle_tool.transcriber import transcribe_video

logger = logging.getLogger(__name__)


@dataclass
class ProcessingResult:
    """Result of processing a single video."""

    video_info: VideoInfo
    output_path: Path | None = None
    error: str | None = None
    quality_score: float | None = None

    @property
    def success(self) -> bool:
        return self.output_path is not None and self.error is None


def process_single_video(
    url: str,
    config: AppConfig,
    progress_callback: callable | None = None,
) -> ProcessingResult:
    """Full pipeline for a single video: download → transcribe → burn.

    Args:
        progress_callback: Optional callback(current_sec, total_sec, speed_str)
                          for real-time encoding progress updates (used by GUI).
    """
    video_info = VideoInfo(title="unknown", duration=0, video_id="", url=url)

    try:
        # Step 1: Download
        logger.info("=" * 60)
        logger.info("Step 1/3: Downloading video...")
        video_info = download_video(url, config)

        if video_info.filepath is None:
            raise SubtitleToolError("Download succeeded but no file path returned.")

        # Step 2: Transcribe
        logger.info("Step 2/3: Transcribing audio...")
        if video_info.subtitle_path and video_info.subtitle_path.exists():
            logger.info("Using YouTube auto-subtitle instead of Whisper.")
            srt_path = video_info.subtitle_path
        else:
            srt_path = transcribe_video(video_info.filepath, config)

        # Normalize SRT: single line per entry
        normalize_srt_single_line(srt_path)

        # Quality check
        quality_report = check_quality(srt_path, log_output=True)

        # Generate drawtext filter script (same style as Daisy watermark)
        style = config.caption_style
        filter_script = generate_subtitle_filterscript(
            srt_path,
            watermark_filter=config.watermark.to_drawtext_filter(),
            font_name=style.font_name,
            font_size=style.font_size,
            font_color=style.font_color,
            box_color=style.box_color,
            box_opacity=style.box_opacity,
            box_border_w=style.box_border_w,
            margin_v=style.margin_v,
        )

        # Step 3: Process (burn subs + watermark)
        logger.info("Step 3/3: Burning subtitles + watermark...")
        # Sanitize filename: remove chars that break FFmpeg on Windows
        safe_name = re.sub(r'[<>:"/\\|?*$!&\'`]', '', video_info.filepath.stem)
        safe_name = safe_name.strip().replace("  ", " ")
        output_path = config.output_dir / f"{safe_name}_subtitled.mp4"
        burn_with_filterscript(
            video_info.filepath, filter_script, output_path, config,
            duration=video_info.duration,
            progress_callback=progress_callback,
        )

        logger.info("✅ Done: %s", output_path.name)

        # Clean up intermediate files (keep source video for potential re-use)
        cleanup_temp_files(filter_script)

        return ProcessingResult(
            video_info=video_info,
            output_path=output_path,
            quality_score=quality_report.score,
        )

    except ValidationError as exc:
        logger.warning("⏭ Skipped: %s", exc)
        return ProcessingResult(video_info=video_info, error=str(exc))

    except SubtitleToolError as exc:
        logger.error("❌ Failed: %s", exc)
        return ProcessingResult(video_info=video_info, error=str(exc))

    except Exception as exc:
        logger.error("❌ Unexpected error: %s", exc)
        return ProcessingResult(video_info=video_info, error=str(exc))


def process_channel(channel_url: str, config: AppConfig) -> list[ProcessingResult]:
    """Process multiple videos from a YouTube channel."""
    logger.info("Fetching video list from channel...")
    urls = list_channel_videos(channel_url, max_count=config.max_videos)

    if not urls:
        logger.warning("No videos found in channel.")
        return []

    logger.info("Found %d videos. Processing...", len(urls))
    results: list[ProcessingResult] = []

    for i, url in enumerate(urls, 1):
        logger.info("\n[%d/%d] %s", i, len(urls), url)
        result = process_single_video(url, config)
        results.append(result)

    # Summary
    success_count = sum(1 for r in results if r.success)
    logger.info("\n" + "=" * 60)
    logger.info("SUMMARY: %d/%d videos processed successfully.", success_count, len(results))

    for result in results:
        status = "✅" if result.success else "❌"
        logger.info("  %s %s", status, result.video_info.title)

    return results
