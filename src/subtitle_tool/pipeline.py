"""Pipeline orchestrator: download → transcribe → process with multi-threading support."""

from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from subtitle_tool.config import AppConfig
from subtitle_tool.downloader import VideoInfo, download_video, list_channel_videos
from subtitle_tool.exceptions import SubtitleToolError, ValidationError
from subtitle_tool.processor import burn_with_filterscript, cleanup_temp_files
from subtitle_tool.quality import check_quality
from subtitle_tool.srt_utils import (
    export_to_json,
    export_to_vtt,
    generate_subtitle_filterscript,
    normalize_srt_single_line,
)
from subtitle_tool.transcriber import transcribe_video

logger = logging.getLogger(__name__)

# Lock to serialize GPU-bound Whisper transcription
# Multiple concurrent Whisper calls can OOM or corrupt shared GPU state
_whisper_lock = threading.Lock()


@dataclass
class ProcessingResult:
    """Result of processing a single video."""

    video_info: VideoInfo
    output_path: Path | None = None
    error: str | None = None
    quality_score: float | None = None
    quality_grade: str | None = None
    export_paths: dict[str, Path] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.output_path is not None and self.error is None


def process_single_video(
    url: str,
    config: AppConfig,
    progress_callback: callable | None = None,
    worker_id: int = 0,
) -> ProcessingResult:
    """Full pipeline for a single video: download → transcribe → burn.

    Args:
        progress_callback: Optional callback(current_sec, total_sec, speed_str)
                          for real-time encoding progress updates (used by GUI).
        worker_id: Worker thread ID for logging (0 = main/single thread).
    """
    video_info = VideoInfo(title="unknown", duration=0, video_id="", url=url)
    prefix = f"[W{worker_id}]" if worker_id > 0 else ""

    try:
        # Validate config before processing
        config.validate()

        # Use worker-specific temp directory to prevent file collisions in parallel mode
        if worker_id > 0:
            worker_temp = config.temp_dir / f"worker_{worker_id}"
            worker_temp.mkdir(parents=True, exist_ok=True)
            # Create a worker-specific config copy to avoid mutating shared config
            from copy import copy
            config = copy(config)
            config.temp_dir = worker_temp

        # Step 1: Download (network I/O — safe for parallel)
        logger.info("%s ═══ Step 1/3: Downloading video...", prefix)
        video_info = download_video(url, config)

        if video_info.filepath is None:
            raise SubtitleToolError("Download succeeded but no file path returned.")

        # Step 2: Transcribe (GPU/CPU bound — serialize with lock)
        logger.info("%s Step 2/3: Transcribing audio...", prefix)
        if video_info.subtitle_path and video_info.subtitle_path.exists():
            logger.info("%s Using YouTube auto-subtitle instead of Whisper.", prefix)
            srt_path = video_info.subtitle_path
        else:
            # Acquire lock to prevent concurrent Whisper model loading
            logger.debug("%s Waiting for Whisper lock...", prefix)
            with _whisper_lock:
                logger.debug("%s Got Whisper lock, starting transcription.", prefix)
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

        # Step 3: Process (burn subs + watermark — CPU bound)
        logger.info("%s Step 3/3: Burning subtitles + watermark...", prefix)
        # Sanitize filename: remove chars that break FFmpeg on Windows
        safe_name = re.sub(r'[<>:"/\\|?*$!&\'`]', '', video_info.filepath.stem)
        safe_name = safe_name.strip().replace("  ", " ")
        output_path = config.output_dir / f"{safe_name}_subtitled.mp4"
        burn_with_filterscript(
            video_info.filepath, filter_script, output_path, config,
            duration=video_info.duration,
            progress_callback=progress_callback,
        )

        logger.info("%s ✅ Done: %s", prefix, output_path.name)

        # Export additional formats if configured
        export_paths: dict[str, Path] = {"srt": srt_path}
        if config.export_format in ("vtt", "both"):
            vtt_out = config.output_dir / f"{safe_name}.vtt"
            export_to_vtt(srt_path, vtt_out)
            export_paths["vtt"] = vtt_out
        if config.export_format == "both":
            json_out = config.output_dir / f"{safe_name}.json"
            export_to_json(srt_path, json_out)
            export_paths["json"] = json_out

        # Clean up intermediate temp files
        temp_files = [filter_script]
        if video_info.filepath and video_info.filepath.exists():
            temp_files.append(video_info.filepath)
        if video_info.subtitle_path and video_info.subtitle_path.exists():
            temp_files.append(video_info.subtitle_path)
        # Also clean the normalized SRT in temp dir (different from export SRT)
        if srt_path.parent == config.temp_dir and srt_path.exists():
            temp_files.append(srt_path)
        cleanup_temp_files(*temp_files)
        # Clean worker temp dir if empty
        if worker_id > 0 and config.temp_dir.exists():
            try:
                config.temp_dir.rmdir()  # Only removes if empty
            except OSError:
                pass

        return ProcessingResult(
            video_info=video_info,
            output_path=output_path,
            quality_score=quality_report.score,
            quality_grade=quality_report.grade,
            export_paths=export_paths,
        )

    except ValidationError as exc:
        logger.warning("%s ⏭ Skipped: %s", prefix, exc)
        return ProcessingResult(video_info=video_info, error=str(exc))

    except SubtitleToolError as exc:
        logger.error("%s ❌ Failed: %s", prefix, exc)
        return ProcessingResult(video_info=video_info, error=str(exc))

    except Exception as exc:
        logger.error("%s ❌ Unexpected error: %s", prefix, exc)
        return ProcessingResult(video_info=video_info, error=str(exc))


# ═════════════════════════════════════════════════════════════════
# Sequential processing (original behavior)
# ═════════════════════════════════════════════════════════════════


def process_channel(channel_url: str, config: AppConfig) -> list[ProcessingResult]:
    """Process multiple videos from a YouTube channel (sequential)."""
    logger.info("Fetching video list from channel...")
    urls = list_channel_videos(channel_url, max_count=config.max_videos)

    if not urls:
        logger.warning("No videos found in channel.")
        return []

    logger.info("Found %d videos. Processing sequentially...", len(urls))
    results: list[ProcessingResult] = []

    for i, url in enumerate(urls, 1):
        logger.info("\n[%d/%d] %s", i, len(urls), url)
        result = process_single_video(url, config)
        results.append(result)

    _log_summary(results)
    return results


# ═════════════════════════════════════════════════════════════════
# Multi-threaded processing
# ═════════════════════════════════════════════════════════════════

DEFAULT_WORKERS = 2
MAX_WORKERS = 6


def process_channel_parallel(
    channel_url: str,
    config: AppConfig,
    max_workers: int = DEFAULT_WORKERS,
    progress_callback: callable | None = None,
) -> list[ProcessingResult]:
    """Process multiple videos from a YouTube channel using thread pool.

    Args:
        channel_url: YouTube channel URL.
        config: Application configuration.
        max_workers: Number of concurrent worker threads (default: 2, max: 6).
        progress_callback: Optional callback(worker_id, video_idx, total, result)
                          for per-video completion updates.

    Returns:
        List of ProcessingResult in original URL order.
    """
    logger.info("Fetching video list from channel...")
    urls = list_channel_videos(channel_url, max_count=config.max_videos)

    if not urls:
        logger.warning("No videos found in channel.")
        return []

    return process_urls_parallel(urls, config, max_workers, progress_callback)


def process_urls_parallel(
    urls: list[str],
    config: AppConfig,
    max_workers: int = DEFAULT_WORKERS,
    progress_callback: callable | None = None,
) -> list[ProcessingResult]:
    """Process a list of YouTube URLs concurrently using a thread pool.

    Each worker handles the full pipeline (download → transcribe → burn)
    for one video at a time. Whisper transcription is serialized via a lock
    to prevent GPU OOM errors.

    Args:
        urls: List of YouTube video URLs.
        config: Application configuration.
        max_workers: Number of concurrent worker threads (1-6).
        progress_callback: Optional callback(worker_id, video_idx, total, result)
                          called when each video completes.

    Returns:
        List of ProcessingResult in original URL order.

    Threading strategy:
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │ Worker 1 │  │ Worker 2 │  │ Worker 3 │
        │ Download │  │ Download │  │ Download │  ← parallel (network I/O)
        │ Transcr. │  │ (wait)   │  │ (wait)   │  ← serialized (GPU lock)
        │ Encode   │  │ Transcr. │  │ (wait)   │  ← parallel (CPU, ffmpeg)
        │ ✅ Done  │  │ Encode   │  │ Transcr. │
        │          │  │ ✅ Done  │  │ Encode   │
        │          │  │          │  │ ✅ Done  │
        └──────────┘  └──────────┘  └──────────┘
    """
    # Clamp workers
    max_workers = max(1, min(max_workers, MAX_WORKERS))
    total = len(urls)

    if max_workers == 1:
        logger.info("Processing %d videos sequentially (workers=1)...", total)
        results = []
        for i, url in enumerate(urls, 1):
            logger.info("\n[%d/%d] %s", i, total, url)
            result = process_single_video(url, config)
            results.append(result)
            if progress_callback:
                progress_callback(0, i, total, result)
        _log_summary(results)
        return results

    logger.info(
        "🚀 Processing %d videos with %d workers (multi-threaded)...",
        total, max_workers,
    )

    # Results dict preserves order: {url_index: result}
    results_map: dict[int, ProcessingResult] = {}
    completed = 0
    completed_lock = threading.Lock()

    def _worker(url: str, idx: int, worker_id: int) -> tuple[int, ProcessingResult]:
        """Worker function for thread pool."""
        logger.info("[W%d] Starting video %d/%d: %s", worker_id, idx + 1, total, url)
        result = process_single_video(url, config, worker_id=worker_id)
        return idx, result

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_idx = {}
        for idx, url in enumerate(urls):
            worker_id = (idx % max_workers) + 1
            future = executor.submit(_worker, url, idx, worker_id)
            future_to_idx[future] = idx

        # Collect results as they complete
        for future in as_completed(future_to_idx):
            try:
                idx, result = future.result()
                results_map[idx] = result
                with completed_lock:
                    completed += 1
                    status = "✅" if result.success else "❌"
                    logger.info(
                        "%s [%d/%d completed] %s",
                        status, completed, total, result.video_info.title,
                    )
                if progress_callback:
                    progress_callback(
                        (idx % max_workers) + 1,
                        completed, total, result,
                    )
            except Exception as exc:
                idx = future_to_idx[future]
                logger.error("❌ Worker crashed for video %d: %s", idx + 1, exc)
                results_map[idx] = ProcessingResult(
                    video_info=VideoInfo(
                        title=f"Video {idx + 1}", duration=0,
                        video_id="", url=urls[idx],
                    ),
                    error=str(exc),
                )
                with completed_lock:
                    completed += 1

    # Return results in original order
    results = [results_map[i] for i in range(total)]
    _log_summary(results)
    return results


def _log_summary(results: list[ProcessingResult]) -> None:
    """Log processing summary."""
    total = len(results)
    success_count = sum(1 for r in results if r.success)
    logger.info("\n" + "═" * 60)
    logger.info("SUMMARY: %d/%d videos processed successfully.", success_count, total)

    for result in results:
        status = "✅" if result.success else "❌"
        grade = f" (Q:{result.quality_score:.0f}/{result.quality_grade})" if result.quality_score else ""
        logger.info("  %s %s%s", status, result.video_info.title, grade)
