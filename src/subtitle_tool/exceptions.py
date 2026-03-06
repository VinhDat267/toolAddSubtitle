"""Custom exception hierarchy for subtitle-tool with retry support."""

from __future__ import annotations

import functools
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class SubtitleToolError(Exception):
    """Base exception for all subtitle-tool errors."""

    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.context = context or {}

    def __str__(self) -> str:
        base = super().__str__()
        if self.context:
            details = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{base} [{details}]"
        return base


class DownloadError(SubtitleToolError):
    """Raised when YouTube video download fails."""


class TranscriptionError(SubtitleToolError):
    """Raised when Whisper transcription fails."""


class ProcessingError(SubtitleToolError):
    """Raised when FFmpeg processing fails."""


class ValidationError(SubtitleToolError):
    """Raised when input validation fails (invalid URL, duration exceeded, etc)."""


class ConfigurationError(SubtitleToolError):
    """Raised when configuration is invalid or missing."""


class RetryExhaustedError(SubtitleToolError):
    """Raised after all retry attempts are exhausted."""

    def __init__(self, message: str, attempts: int, last_error: Exception) -> None:
        super().__init__(message, context={"attempts": attempts})
        self.attempts = attempts
        self.last_error = last_error


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
):
    """Decorator that retries a function on failure with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts (including first try).
        delay: Initial delay in seconds between retries.
        backoff: Multiplier for delay after each retry.
        exceptions: Tuple of exception types to catch for retry.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exc = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        logger.error(
                            "❌ %s failed after %d attempts: %s",
                            func.__name__, max_attempts, exc,
                        )
                        raise RetryExhaustedError(
                            f"{func.__name__} failed after {max_attempts} attempts: {exc}",
                            attempts=max_attempts,
                            last_error=exc,
                        ) from exc
                    logger.warning(
                        "⚠️  %s attempt %d/%d failed: %s — retrying in %.1fs...",
                        func.__name__, attempt, max_attempts, exc, current_delay,
                    )
                    time.sleep(current_delay)
                    current_delay *= backoff

        return wrapper

    return decorator
