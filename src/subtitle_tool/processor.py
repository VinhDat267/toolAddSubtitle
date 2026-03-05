"""FFmpeg video processor: burn subtitles + watermark."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from subtitle_tool.config import AppConfig
from subtitle_tool.exceptions import ProcessingError

logger = logging.getLogger(__name__)


def check_ffmpeg(ffmpeg_path: str = "ffmpeg") -> bool:
    """Verify FFmpeg is installed and accessible."""
    try:
        result = subprocess.run(
            [ffmpeg_path, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _get_duration_seconds(video_path: Path, ffmpeg_path: str) -> float:
    """Get video duration in seconds using ffprobe/ffmpeg."""
    # Derive ffprobe path: replace only the filename, not folder names
    ffmpeg_file = Path(ffmpeg_path)
    ffprobe = str(ffmpeg_file.parent / ffmpeg_file.name.replace("ffmpeg", "ffprobe"))
    try:
        result = subprocess.run(
            [ffprobe, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return 0.0


def _parse_ffmpeg_time(time_str: str) -> float:
    """Parse FFmpeg time string (HH:MM:SS.xx) to seconds."""
    parts = time_str.split(":")
    if len(parts) == 3:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
        return hours * 3600 + minutes * 60 + seconds
    return 0.0


def _print_progress(current_sec: float, total_sec: float, speed: str) -> None:
    """Print a progress bar to the console."""
    if total_sec <= 0:
        return
    pct = min(current_sec / total_sec * 100, 100)
    bar_len = 30
    filled = int(bar_len * pct / 100)
    bar = "█" * filled + "░" * (bar_len - filled)

    # Format elapsed time
    mins = int(current_sec) // 60
    secs = int(current_sec) % 60
    total_mins = int(total_sec) // 60
    total_secs = int(total_sec) % 60

    line = (
        f"  ⏳ [{bar}] {pct:5.1f}%  "
        f"{mins:02d}:{secs:02d}/{total_mins:02d}:{total_secs:02d}  "
        f"speed={speed}  "
    )
    print(f"\r{line}", end="", flush=True)


def burn_with_filterscript(
    video_path: Path,
    filter_script: Path,
    output_path: Path,
    config: AppConfig,
    duration: float = 0,
    progress_callback: callable | None = None,
) -> Path:
    """Burn subtitles + watermark using a filter_script file (drawtext approach).

    Uses -filter_script:v to load drawtext filters from a file,
    avoiding Windows command line length limits.
    Shows real-time progress bar during encoding.

    Args:
        progress_callback: Optional callback(current_sec, total_sec, speed_str)
                          called on each FFmpeg progress update. If None, prints
                          to console (CLI mode).
    """
    if not check_ffmpeg(config.ffmpeg_path):
        raise ProcessingError(
            f"FFmpeg not found at '{config.ffmpeg_path}'. "
            "Install it: https://www.gyan.dev/ffmpeg/builds/ or choco install ffmpeg"
        )

    # Use provided duration (seconds), fallback to ffprobe
    total_duration = duration if duration > 0 else _get_duration_seconds(video_path, config.ffmpeg_path)

    cmd = [
        config.ffmpeg_path,
        "-i", str(video_path),
        "-filter_script:v", str(filter_script),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-y",
        str(output_path),
    ]

    logger.info("Processing video with FFmpeg (drawtext mode)...")
    logger.debug("FFmpeg command: %s", " ".join(cmd))

    # Use Popen to stream stderr in real-time for progress
    process: subprocess.Popen | None = None
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        stderr_lines: list[str] = []
        time_pattern = re.compile(r"time=(\d{2}:\d{2}:\d{2}\.\d+)")
        speed_pattern = re.compile(r"speed=\s*([\d.]+x)")

        # Read stderr line by line (FFmpeg uses \r for progress)
        if process.stderr is None:
            raise ProcessingError("FFmpeg stderr stream is not available.")

        buffer = ""
        while True:
            char = process.stderr.read(1)
            if not char:
                break
            buffer += char
            # FFmpeg uses \r to update progress on same line
            if char in ("\r", "\n"):
                line = buffer.strip()
                if line:
                    stderr_lines.append(line)
                    # Parse progress info
                    time_match = time_pattern.search(line)
                    speed_match = speed_pattern.search(line)
                    if time_match and total_duration > 0:
                        current_sec = _parse_ffmpeg_time(time_match.group(1))
                        speed = speed_match.group(1) if speed_match else "?"
                        if progress_callback:
                            progress_callback(current_sec, total_duration, speed)
                        else:
                            _print_progress(current_sec, total_duration, speed)
                buffer = ""

        process.wait(timeout=60)

        # Clear progress line (CLI mode only)
        if total_duration > 0 and not progress_callback:
            print()  # New line after progress bar

    except subprocess.TimeoutExpired:
        if process is not None:
            process.kill()
            process.communicate()  # Drain remaining output
        raise ProcessingError("FFmpeg timed out.")
    except ProcessingError:
        raise
    except Exception as exc:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()
        raise ProcessingError(f"FFmpeg execution failed: {exc}") from exc

    if process.returncode != 0:
        error_lines = [l for l in stderr_lines[-10:] if l]
        error_msg = "\n".join(error_lines)
        raise ProcessingError(f"FFmpeg failed (exit code {process.returncode}):\n{error_msg}")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise ProcessingError(f"Output file is missing or empty: {output_path}")

    output_size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info("Output saved: %s (%.1f MB)", output_path.name, output_size_mb)
    return output_path


def cleanup_temp_files(*paths: Path) -> None:
    """Remove temporary files, ignoring errors."""
    for path in paths:
        try:
            if path.is_file():
                path.unlink()
                logger.debug("Cleaned up: %s", path)
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass
