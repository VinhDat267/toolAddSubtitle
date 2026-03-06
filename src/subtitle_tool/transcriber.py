"""Speech-to-text transcription using faster-whisper with multi-language support."""

from __future__ import annotations

import logging
from pathlib import Path

from subtitle_tool.config import AppConfig, SUPPORTED_LANGUAGES
from subtitle_tool.exceptions import TranscriptionError, retry

logger = logging.getLogger(__name__)


@retry(max_attempts=2, delay=2.0, exceptions=(TranscriptionError,))
def transcribe_video(video_path: Path, config: AppConfig) -> Path:
    """Transcribe video audio to an SRT subtitle file.

    Uses faster-whisper for local, offline transcription.
    Supports multi-language transcription and auto-detection.
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
    language = config.whisper.language

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
        raise TranscriptionError(
            f"Failed to load Whisper model: {exc}",
            context={"model": config.whisper.model_size, "device": device},
        ) from exc

    # Determine language parameter
    lang_param = None if language == "auto" else language
    lang_display = SUPPORTED_LANGUAGES.get(language, language)

    if language == "auto":
        logger.info("🌍 Auto-detecting language for '%s'...", video_path.name)
    else:
        logger.info("🗣️ Transcribing '%s' in %s...", video_path.name, lang_display)

    try:
        segments, info = model.transcribe(
            str(video_path),
            language=lang_param,
            vad_filter=True,  # Filter out silence
            vad_parameters={"min_silence_duration_ms": 500},
        )
    except Exception as exc:
        raise TranscriptionError(
            f"Transcription failed: {exc}",
            context={"file": video_path.name, "language": language},
        ) from exc

    detected_lang = info.language
    detected_prob = info.language_probability
    lang_name = SUPPORTED_LANGUAGES.get(detected_lang, detected_lang)

    logger.info(
        "🔍 Detected language: %s (%s) — confidence: %.1f%%",
        lang_name, detected_lang, detected_prob * 100,
    )

    # Warn if confidence is low
    if detected_prob < 0.5:
        logger.warning(
            "⚠️  Low language confidence (%.1f%%). Subtitle accuracy may be affected.",
            detected_prob * 100,
        )

    # Write SRT file
    srt_content = _segments_to_srt(segments)
    if not srt_content.strip():
        raise TranscriptionError(
            "Transcription produced no text segments.",
            context={"file": video_path.name, "language": detected_lang},
        )

    srt_path.write_text(srt_content, encoding="utf-8")

    # Count entries for logging
    entry_count = srt_content.count("\n\n")
    logger.info(
        "✅ Subtitles saved: %s (%d entries, language: %s)",
        srt_path.name, entry_count, lang_name,
    )
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
