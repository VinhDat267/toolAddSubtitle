"""Dataclass-based configuration for subtitle-tool with validation."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from subtitle_tool.exceptions import ConfigurationError

logger = logging.getLogger(__name__)

# ─── Supported languages ─────────────────────────────────────────
SUPPORTED_LANGUAGES = {
    "auto": "Auto-detect",
    "en": "English",
    "vi": "Vietnamese",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "pt": "Portuguese",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
    "th": "Thai",
    "id": "Indonesian",
    "it": "Italian",
    "nl": "Dutch",
    "pl": "Polish",
    "tr": "Turkish",
    "sv": "Swedish",
}

WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3")
COMPUTE_DEVICES = ("auto", "cpu", "cuda")
EXPORT_FORMATS = ("srt", "vtt", "both")


@dataclass
class WatermarkConfig:
    """Watermark text overlay settings."""

    text: str = "Daisy"
    font_name: str = "Arial"
    position_x: str = "w-tw-20"  # 20px from right edge
    position_y: str = "20"  # 20px from top
    font_size: int = 28
    font_color: str = "white"
    box_enabled: bool = True
    box_color: str = "black"
    box_opacity: float = 0.6  # 60% opacity
    box_border_w: int = 10  # Padding around text

    def to_drawtext_filter(self) -> str:
        # Escape special chars for FFmpeg drawtext
        safe_text = self.text.replace("'", "\u2019")  # Smart quote avoids FFmpeg quoting issues
        safe_text = safe_text.replace("\\", "\\\\")
        safe_text = safe_text.replace(":", "\\:")
        safe_text = safe_text.replace(",", "\\,")
        safe_text = safe_text.replace(";", "\\;")

        parts = [
            f"drawtext=text='{safe_text}'",
            f"font={self.font_name}",
            f"x={self.position_x}",
            f"y={self.position_y}",
            f"fontsize={self.font_size}",
            f"fontcolor={self.font_color}",
        ]
        if self.box_enabled:
            parts.append("box=1")
            parts.append(f"boxcolor={self.box_color}@{self.box_opacity}")
            parts.append(f"boxborderw={self.box_border_w}")
        return ":".join(parts)


@dataclass
class CaptionStyle:
    """Subtitle burn-in style — uses drawtext (same as watermark) for reliable alpha."""

    font_name: str = "Arial"
    font_size: int = 28
    font_color: str = "white"
    box_color: str = "black"
    box_opacity: float = 0.6  # 60% opacity background
    box_border_w: int = 8  # Padding around text
    margin_v: int = 30  # Pixels from bottom edge


@dataclass
class WhisperConfig:
    """Whisper transcription settings with multi-language support."""

    model_size: str = "medium"
    language: str = "en"
    device: str = "auto"  # "auto", "cpu", or "cuda"
    compute_type: str = "auto"  # "auto", "int8", "float16", "float32"

    def resolve_device(self) -> str:
        if self.device != "auto":
            return self.device
        try:
            import torch
            return "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"

    def resolve_compute_type(self) -> str:
        if self.compute_type != "auto":
            return self.compute_type
        return "float16" if self.resolve_device() == "cuda" else "int8"

    def validate(self) -> None:
        """Validate whisper configuration."""
        if self.model_size not in WHISPER_MODELS:
            raise ConfigurationError(
                f"Invalid model '{self.model_size}'. "
                f"Supported: {', '.join(WHISPER_MODELS)}"
            )
        if self.device not in COMPUTE_DEVICES:
            raise ConfigurationError(
                f"Invalid device '{self.device}'. "
                f"Supported: {', '.join(COMPUTE_DEVICES)}"
            )
        if self.language != "auto" and self.language not in SUPPORTED_LANGUAGES:
            raise ConfigurationError(
                f"Invalid language '{self.language}'. "
                f"Supported: {', '.join(SUPPORTED_LANGUAGES.keys())}"
            )


MAX_DURATION_SECONDS = 26 * 60  # 26 minutes
HD_THRESHOLD_SECONDS = 10 * 60  # 10 minutes


def get_quality_for_duration(duration_seconds: float) -> int:
    """Return target video height (720 or 1080) based on duration."""
    if duration_seconds <= HD_THRESHOLD_SECONDS:
        return 1080
    return 720


@dataclass
class AppConfig:
    """Top-level application configuration."""

    output_dir: Path = field(default_factory=lambda: Path("./output"))
    temp_dir: Path = field(default_factory=lambda: Path("./temp"))
    watermark: WatermarkConfig = field(default_factory=WatermarkConfig)
    caption_style: CaptionStyle = field(default_factory=CaptionStyle)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    max_videos: int = 10
    ffmpeg_path: str = "ffmpeg"
    export_format: str = "srt"  # "srt", "vtt", or "both"

    def __post_init__(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def validate(self) -> None:
        """Validate all configuration settings."""
        self.whisper.validate()

        if self.export_format not in EXPORT_FORMATS:
            raise ConfigurationError(
                f"Invalid export format '{self.export_format}'. "
                f"Supported: {', '.join(EXPORT_FORMATS)}"
            )

        if self.max_videos < 1:
            raise ConfigurationError("max_videos must be at least 1.")

        logger.debug("✅ Configuration validated successfully.")

    @classmethod
    def from_env(cls) -> AppConfig:
        """Create config from environment variables with sensible defaults."""
        config = cls(
            output_dir=Path(os.getenv("SUBTITLE_OUTPUT_DIR", "./output")),
            temp_dir=Path(os.getenv("SUBTITLE_TEMP_DIR", "./temp")),
            whisper=WhisperConfig(
                model_size=os.getenv("WHISPER_MODEL", "medium"),
                language=os.getenv("WHISPER_LANGUAGE", "en"),
                device=os.getenv("WHISPER_DEVICE", "auto"),
            ),
            ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
            export_format=os.getenv("EXPORT_FORMAT", "srt"),
        )
        return config
