"""Speech-to-text transcription using faster-whisper."""

from __future__ import annotations

import logging
from pathlib import Path

from subtitle_tool.config import AppConfig
from subtitle_tool.exceptions import TranscriptionError

logger = logging.getLogger(__name__)


def transcribe_video(video_path: Path, config: AppConfig) -> Path:
    """Transcribe video audio to an SRT subtitle file.

    Uses faster-whisper for local, offline transcription.
    Returns the path to the generated .srt file.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionError(
            "faster-whisper is not installed. Run: pip install faster-whisper"
        ) from exc

    srt_path = video_path.with_suffix(".srt")

    device = config.whisper.resolve_device()
    compute_type = config.whisper.resolve_compute_type()

    logger.info(
        "Loading Whisper model '%s' on %s (%s)...",
        config.whisper.model_size,
        device,
        compute_type,
    )

    try:
        model = WhisperModel(
            config.whisper.model_size,
            device=device,
            compute_type=compute_type,
        )
    except Exception as exc:
        raise TranscriptionError(f"Failed to load Whisper model: {exc}") from exc

    logger.info("Transcribing '%s'...", video_path.name)

    try:
        segments, info = model.transcribe(
            str(video_path),
            language=config.whisper.language,
            vad_filter=True,  # Filter out silence
            vad_parameters={"min_silence_duration_ms": 500},
        )
    except Exception as exc:
        raise TranscriptionError(f"Transcription failed: {exc}") from exc

    logger.info("Detected language: %s (probability %.2f)", info.language, info.language_probability)

    # Write SRT file
    srt_content = _segments_to_srt(segments)
    if not srt_content.strip():
        raise TranscriptionError("Transcription produced no text segments.")

    srt_path.write_text(srt_content, encoding="utf-8")
    logger.info("Subtitles saved: %s", srt_path.name)
    return srt_path


def _segments_to_srt(segments) -> str:
    """Convert whisper segments to SRT format string."""
    lines: list[str] = []
    index = 1

    for segment in segments:
        start = _format_timestamp(segment.start)
        end = _format_timestamp(segment.end)
        text = segment.text.strip()

        if not text:
            continue

        lines.append(f"{index}")
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")  # Blank line separator
        index += 1

    return "\n".join(lines)


def _format_timestamp(seconds: float) -> str:
    """Convert seconds to SRT timestamp format (HH:MM:SS,mmm)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
