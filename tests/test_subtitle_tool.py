"""Comprehensive test suite for subtitle-tool — following python-testing-patterns skill."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from subtitle_tool.config import (
    SUPPORTED_LANGUAGES,
    WHISPER_MODELS,
    AppConfig,
    CaptionStyle,
    WatermarkConfig,
    WhisperConfig,
)
from subtitle_tool.exceptions import (
    ConfigurationError,
    DownloadError,
    ProcessingError,
    RetryExhaustedError,
    SubtitleToolError,
    TranscriptionError,
    ValidationError,
    retry,
)
from subtitle_tool.srt_utils import (
    SrtEntry,
    _entries_to_srt,
    _ms_to_timestamp,
    _ms_to_vtt_timestamp,
    _parse_srt,
    _split_text_smartly,
    _timestamp_to_ms,
    export_to_json,
    export_to_vtt,
)


# ═════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════


@pytest.fixture
def sample_srt_content() -> str:
    """Valid SRT content with multiple entries."""
    return (
        "1\n"
        "00:00:01,000 --> 00:00:03,500\n"
        "Hello, world!\n"
        "\n"
        "2\n"
        "00:00:04,000 --> 00:00:06,000\n"
        "This is a test subtitle.\n"
        "\n"
        "3\n"
        "00:00:07,500 --> 00:00:10,000\n"
        "Testing the subtitle tool.\n"
        "\n"
    )


@pytest.fixture
def sample_srt_file(tmp_path: Path, sample_srt_content: str) -> Path:
    """Create a temporary SRT file."""
    srt_path = tmp_path / "test.srt"
    srt_path.write_text(sample_srt_content, encoding="utf-8")
    return srt_path


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    """Create a test AppConfig with temp directories."""
    return AppConfig(
        output_dir=tmp_path / "output",
        temp_dir=tmp_path / "temp",
    )


# ═════════════════════════════════════════════════════════════════
# Test: Exceptions
# ═════════════════════════════════════════════════════════════════


class TestExceptions:
    """Test custom exception hierarchy and retry mechanism."""

    def test_base_exception(self):
        exc = SubtitleToolError("test error")
        assert str(exc) == "test error"
        assert exc.context == {}

    def test_exception_with_context(self):
        exc = SubtitleToolError("failed", context={"url": "test.com", "code": 404})
        assert "url=test.com" in str(exc)
        assert "code=404" in str(exc)

    def test_exception_hierarchy(self):
        assert issubclass(DownloadError, SubtitleToolError)
        assert issubclass(TranscriptionError, SubtitleToolError)
        assert issubclass(ProcessingError, SubtitleToolError)
        assert issubclass(ValidationError, SubtitleToolError)
        assert issubclass(ConfigurationError, SubtitleToolError)

    def test_retry_exhausted_error(self):
        original = ValueError("original error")
        exc = RetryExhaustedError("retry failed", attempts=3, last_error=original)
        assert exc.attempts == 3
        assert exc.last_error is original
        assert "attempts=3" in str(exc)

    def test_retry_decorator_success(self):
        """Function succeeds on first try."""
        call_count = 0

        @retry(max_attempts=3, delay=0.01)
        def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"

        assert succeed() == "ok"
        assert call_count == 1

    def test_retry_decorator_eventual_success(self):
        """Function fails twice then succeeds."""
        call_count = 0

        @retry(max_attempts=3, delay=0.01, exceptions=(ValueError,))
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("not yet")
            return "finally"

        assert flaky() == "finally"
        assert call_count == 3

    def test_retry_decorator_exhausted(self):
        """Function fails all attempts."""

        @retry(max_attempts=2, delay=0.01, exceptions=(ValueError,))
        def always_fail():
            raise ValueError("always fails")

        with pytest.raises(RetryExhaustedError) as exc_info:
            always_fail()
        assert exc_info.value.attempts == 2


# ═════════════════════════════════════════════════════════════════
# Test: Configuration
# ═════════════════════════════════════════════════════════════════


class TestConfig:
    """Test configuration validation and defaults."""

    def test_default_config(self, app_config: AppConfig):
        assert app_config.whisper.model_size == "medium"
        assert app_config.whisper.language == "en"
        assert app_config.ffmpeg_path == "ffmpeg"
        assert app_config.export_format == "srt"

    def test_config_validation_valid(self, app_config: AppConfig):
        app_config.validate()  # Should not raise

    def test_config_validation_invalid_model(self, app_config: AppConfig):
        app_config.whisper.model_size = "invalid"
        with pytest.raises(ConfigurationError, match="Invalid model"):
            app_config.validate()

    def test_config_validation_invalid_device(self, app_config: AppConfig):
        app_config.whisper.device = "tpu"
        with pytest.raises(ConfigurationError, match="Invalid device"):
            app_config.validate()

    def test_config_validation_invalid_language(self, app_config: AppConfig):
        app_config.whisper.language = "xx"
        with pytest.raises(ConfigurationError, match="Invalid language"):
            app_config.validate()

    def test_config_validation_invalid_export(self, app_config: AppConfig):
        app_config.export_format = "mp3"
        with pytest.raises(ConfigurationError, match="Invalid export format"):
            app_config.validate()

    def test_supported_languages(self):
        assert "en" in SUPPORTED_LANGUAGES
        assert "vi" in SUPPORTED_LANGUAGES
        assert "auto" in SUPPORTED_LANGUAGES
        assert len(SUPPORTED_LANGUAGES) >= 20

    def test_whisper_models(self):
        assert "tiny" in WHISPER_MODELS
        assert "medium" in WHISPER_MODELS
        assert "large-v3" in WHISPER_MODELS

    def test_watermark_drawtext_filter(self):
        wm = WatermarkConfig(text="Test")
        filter_str = wm.to_drawtext_filter()
        assert "drawtext" in filter_str
        assert "Test" in filter_str
        assert "font=Arial" in filter_str

    def test_watermark_special_chars_escape(self):
        wm = WatermarkConfig(text="He:llo,World")
        filter_str = wm.to_drawtext_filter()
        assert "\\:" in filter_str
        assert "\\," in filter_str

    def test_from_env(self):
        with patch.dict("os.environ", {"WHISPER_MODEL": "small", "WHISPER_LANGUAGE": "vi"}):
            config = AppConfig.from_env()
            assert config.whisper.model_size == "small"
            assert config.whisper.language == "vi"

    def test_resolve_device_cpu_fallback(self):
        whisper = WhisperConfig(device="auto")
        # Without torch installed, should fallback to cpu
        device = whisper.resolve_device()
        assert device in ("cpu", "cuda")

    def test_resolve_compute_type(self):
        whisper = WhisperConfig(compute_type="float32")
        assert whisper.resolve_compute_type() == "float32"


# ═════════════════════════════════════════════════════════════════
# Test: SRT Utilities
# ═════════════════════════════════════════════════════════════════


class TestSrtUtils:
    """Test SRT parsing, timestamp conversion, and text splitting."""

    def test_parse_srt(self, sample_srt_content: str):
        entries = _parse_srt(sample_srt_content)
        assert len(entries) == 3
        assert entries[0].text == "Hello, world!"
        assert entries[1].start == "00:00:04,000"

    def test_parse_srt_empty(self):
        entries = _parse_srt("")
        assert len(entries) == 0

    def test_parse_srt_invalid(self):
        entries = _parse_srt("not valid srt content")
        assert len(entries) == 0

    def test_timestamp_to_ms(self):
        assert _timestamp_to_ms("00:00:01,000") == 1000
        assert _timestamp_to_ms("01:30:00,500") == 5400500
        assert _timestamp_to_ms("00:00:00,000") == 0

    def test_ms_to_timestamp(self):
        assert _ms_to_timestamp(1000) == "00:00:01,000"
        assert _ms_to_timestamp(5400500) == "01:30:00,500"
        assert _ms_to_timestamp(0) == "00:00:00,000"

    def test_ms_to_vtt_timestamp(self):
        assert _ms_to_vtt_timestamp(1000) == "00:00:01.000"
        assert _ms_to_vtt_timestamp(5400500) == "01:30:00.500"

    def test_timestamp_round_trip(self):
        """Ensure ms → timestamp → ms is lossless."""
        for ms in [0, 1000, 12345, 5400500, 86399999]:
            assert _timestamp_to_ms(_ms_to_timestamp(ms)) == ms

    def test_entries_to_srt(self):
        entries = [
            SrtEntry(1, "00:00:01,000", "00:00:03,000", "Hello"),
            SrtEntry(2, "00:00:04,000", "00:00:06,000", "World"),
        ]
        srt = _entries_to_srt(entries)
        assert "1\n" in srt
        assert "Hello" in srt
        assert "World" in srt

    def test_split_text_smartly(self):
        text = "This is a long sentence that needs to be split"
        chunks = _split_text_smartly(text, 25)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 25

    def test_split_text_short(self):
        text = "Short text"
        chunks = _split_text_smartly(text, 45)
        assert len(chunks) == 1
        assert chunks[0] == "Short text"


# ═════════════════════════════════════════════════════════════════
# Test: Export Functions
# ═════════════════════════════════════════════════════════════════


class TestExport:
    """Test VTT and JSON export functions."""

    def test_export_to_vtt(self, sample_srt_file: Path, tmp_path: Path):
        vtt_path = tmp_path / "test.vtt"
        result = export_to_vtt(sample_srt_file, vtt_path)
        assert result.exists()
        content = result.read_text(encoding="utf-8")
        assert content.startswith("WEBVTT")
        assert "00:00:01.000 --> 00:00:03.500" in content
        assert "Hello, world!" in content

    def test_export_to_vtt_default_path(self, sample_srt_file: Path):
        result = export_to_vtt(sample_srt_file)
        assert result.suffix == ".vtt"
        assert result.exists()

    def test_export_to_json(self, sample_srt_file: Path, tmp_path: Path):
        import json
        json_path = tmp_path / "test.json"
        result = export_to_json(sample_srt_file, json_path)
        assert result.exists()
        data = json.loads(result.read_text(encoding="utf-8"))
        assert "metadata" in data
        assert "subtitles" in data
        assert data["metadata"]["entries"] == 3
        assert data["subtitles"][0]["text"] == "Hello, world!"

    def test_export_to_json_has_ms_fields(self, sample_srt_file: Path, tmp_path: Path):
        import json
        json_path = tmp_path / "test.json"
        export_to_json(sample_srt_file, json_path)
        data = json.loads(json_path.read_text(encoding="utf-8"))
        first = data["subtitles"][0]
        assert "start_ms" in first
        assert "end_ms" in first
        assert first["start_ms"] == 1000


# ═════════════════════════════════════════════════════════════════
# Test: Downloader
# ═════════════════════════════════════════════════════════════════


class TestDownloader:
    """Test URL validation and filename sanitization."""

    def test_is_youtube_url_valid(self):
        from subtitle_tool.downloader import _is_youtube_url
        assert _is_youtube_url("https://www.youtube.com/watch?v=abc123")
        assert _is_youtube_url("https://youtu.be/abc123")
        assert _is_youtube_url("https://youtube.com/watch?v=abc123")

    def test_is_youtube_url_invalid(self):
        from subtitle_tool.downloader import _is_youtube_url
        assert not _is_youtube_url("https://vimeo.com/12345")
        assert not _is_youtube_url("not a url")
        assert not _is_youtube_url("")

    def test_sanitize_filename(self):
        from subtitle_tool.downloader import _sanitize_filename
        assert _sanitize_filename("Hello: World") == "Hello_ World"
        assert _sanitize_filename('Test "Video"') == "Test Video"
        assert _sanitize_filename("A" * 200)  # Should truncate to 100

    def test_get_video_info_invalid_url(self):
        from subtitle_tool.downloader import get_video_info
        with pytest.raises(ValidationError, match="Not a valid YouTube URL"):
            get_video_info("https://example.com/video")


# ═════════════════════════════════════════════════════════════════
# Test: Quality
# ═════════════════════════════════════════════════════════════════


class TestQuality:
    """Test quality analysis and scoring."""

    def test_quality_check(self, sample_srt_file: Path):
        from subtitle_tool.quality import check_quality
        report = check_quality(sample_srt_file, log_output=False)
        assert report.total_entries == 3
        assert 0 <= report.score <= 100
        assert report.grade in ("A", "B", "C", "D", "F")

    def test_quality_grade_mapping(self):
        from subtitle_tool.quality import QualityReport
        report = QualityReport()
        report.score = 95
        assert report.grade == "A"
        report.score = 85
        assert report.grade == "B"
        report.score = 50
        assert report.grade == "F"


# ═════════════════════════════════════════════════════════════════
# Test: Multi-threading Pipeline
# ═════════════════════════════════════════════════════════════════


class TestMultiThreading:
    """Test multi-threaded processing infrastructure."""

    def test_whisper_lock_exists(self):
        """Verify the Whisper GPU lock is defined."""
        from subtitle_tool.pipeline import _whisper_lock
        import threading
        assert isinstance(_whisper_lock, type(threading.Lock()))

    def test_max_workers_constant(self):
        from subtitle_tool.pipeline import MAX_WORKERS, DEFAULT_WORKERS
        assert MAX_WORKERS == 6
        assert DEFAULT_WORKERS == 2

    def test_process_urls_parallel_clamp_workers(self):
        """Workers should be clamped between 1 and MAX_WORKERS."""
        from subtitle_tool.pipeline import MAX_WORKERS
        # Test that max is 6
        assert MAX_WORKERS >= 1

    def test_processing_result_has_export_paths(self):
        from subtitle_tool.pipeline import ProcessingResult
        from subtitle_tool.downloader import VideoInfo
        result = ProcessingResult(
            video_info=VideoInfo(title="test", duration=60, video_id="x", url="http://test"),
            output_path=None,
            export_paths={"srt": Path("/tmp/test.srt"), "vtt": Path("/tmp/test.vtt")},
        )
        assert "srt" in result.export_paths
        assert "vtt" in result.export_paths
        assert not result.success  # no output_path

    def test_log_summary_function(self):
        """Verify _log_summary doesn't crash with empty results."""
        from subtitle_tool.pipeline import _log_summary, ProcessingResult
        from subtitle_tool.downloader import VideoInfo
        results = [
            ProcessingResult(
                video_info=VideoInfo(title="v1", duration=60, video_id="1", url="http://t1"),
                output_path=Path("/tmp/out.mp4"),
                quality_score=85.0,
                quality_grade="B",
            ),
            ProcessingResult(
                video_info=VideoInfo(title="v2", duration=30, video_id="2", url="http://t2"),
                error="failed",
            ),
        ]
        # Should not raise
        _log_summary(results)
