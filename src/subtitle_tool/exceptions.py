"""Custom exception hierarchy for subtitle-tool."""


class SubtitleToolError(Exception):
    """Base exception for all subtitle-tool errors."""


class DownloadError(SubtitleToolError):
    """Raised when YouTube video download fails."""


class TranscriptionError(SubtitleToolError):
    """Raised when Whisper transcription fails."""


class ProcessingError(SubtitleToolError):
    """Raised when FFmpeg processing fails."""


class ValidationError(SubtitleToolError):
    """Raised when input validation fails (invalid URL, duration exceeded, etc)."""
