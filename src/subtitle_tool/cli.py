"""CLI entry point for subtitle-tool."""

from __future__ import annotations

import argparse
import logging
import sys

from subtitle_tool import __version__
from subtitle_tool.config import AppConfig, WatermarkConfig, WhisperConfig
from subtitle_tool.pipeline import process_channel, process_single_video
from subtitle_tool.processor import check_ffmpeg


def setup_logging(verbose: bool = False) -> None:
    """Configure logging with colored output."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="subtitle-tool",
        description="Auto-add English subtitles + watermark to YouTube videos",
    )

    # Input source (mutually exclusive)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--url", help="Single YouTube video URL")
    source.add_argument("--channel", help="YouTube channel URL to batch process")
    source.add_argument("--check-quality", metavar="SRT_FILE",
                        help="Analyze SRT file quality (standalone)")

    # Options
    parser.add_argument("--output", "-o", default="./output", help="Output directory (default: ./output)")
    parser.add_argument("--max", type=int, default=10, help="Max videos from channel (default: 10)")
    parser.add_argument("--watermark", "-w", default="Daisy", help="Watermark text (default: Daisy)")
    parser.add_argument(
        "--model",
        choices=["tiny", "base", "small", "medium", "large-v3"],
        default="medium",
        help="Whisper model size (default: medium)",
    )
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto", help="Compute device")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="Path to FFmpeg binary")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    return parser


def main() -> None:
    """CLI main entry point."""
    parser = build_parser()
    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    # Check FFmpeg
    if not check_ffmpeg(args.ffmpeg):
        logger.error(
            "FFmpeg not found! Install it:\n"
            "  Windows: choco install ffmpeg  (or download from gyan.dev)\n"
            "  macOS:   brew install ffmpeg\n"
            "  Linux:   sudo apt install ffmpeg"
        )
        sys.exit(1)

    # Build config
    config = AppConfig.from_env()
    config.output_dir = config.output_dir.__class__(args.output)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    config.max_videos = args.max
    config.ffmpeg_path = args.ffmpeg
    config.watermark = WatermarkConfig(text=args.watermark)
    config.whisper = WhisperConfig(model_size=args.model, device=args.device)

    # Standalone quality check mode
    if args.check_quality:
        from pathlib import Path
        from subtitle_tool.quality import check_quality
        srt_file = Path(args.check_quality)
        if not srt_file.exists():
            logger.error("SRT file not found: %s", srt_file)
            sys.exit(1)
        report = check_quality(srt_file, log_output=True)
        sys.exit(0 if report.score >= 60 else 1)

    if not args.url and not args.channel:
        parser.error("one of --url, --channel, or --check-quality is required")

    logger.info("Subtitle Tool v%s", __version__)
    logger.info("Watermark: '%s' | Model: %s | Device: %s", args.watermark, args.model, args.device)

    # Process
    if args.url:
        result = process_single_video(args.url, config)
        sys.exit(0 if result.success else 1)
    else:
        results = process_channel(args.channel, config)
        success_count = sum(1 for r in results if r.success)
        sys.exit(0 if success_count > 0 else 1)


if __name__ == "__main__":
    main()
