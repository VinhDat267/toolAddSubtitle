"""SRT subtitle processing utilities with VTT export support."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


MAX_CHARS_PER_LINE = 45  # Max chars to ensure single line on 720p


def normalize_srt_single_line(srt_path: Path) -> Path:
    """Rewrite SRT so each entry fits on exactly one line on screen.

    - Joins multi-line text into one string
    - Splits long entries into shorter timed segments (~45 chars)
    - Removes YouTube auto-sub artifacts (>> markers)
    """
    content = srt_path.read_text(encoding="utf-8", errors="replace")
    entries = _parse_srt(content)

    normalized: list[SrtEntry] = []
    for entry in entries:
        # Join all text lines into one string
        single_line = " ".join(line.strip() for line in entry.text.split("\n") if line.strip())
        # Clean YouTube auto-sub artifacts
        single_line = re.sub(r"^>>\s*", "", single_line).strip()
        # Remove non-speech annotations: [music], [applause], [laughter], etc.
        single_line = re.sub(r"\[.*?\]", "", single_line).strip()
        single_line = re.sub(r"\s+", " ", single_line)
        if not single_line:
            continue

        # Split long text into chunks that fit one screen line
        if len(single_line) <= MAX_CHARS_PER_LINE:
            entry.text = single_line
            normalized.append(entry)
        else:
            chunks = _split_text_smartly(single_line, MAX_CHARS_PER_LINE)
            sub_entries = _distribute_timestamps(entry.start, entry.end, chunks)
            normalized.extend(sub_entries)

    # Remove timestamp overlaps (YouTube auto-subs often overlap)
    normalized = _deoverlap(normalized)

    output = _entries_to_srt(normalized)
    srt_path.write_text(output, encoding="utf-8")
    logger.info("Normalized %d subtitle entries to single-line.", len(normalized))
    return srt_path



def _deoverlap(entries: list[SrtEntry]) -> list[SrtEntry]:
    """Ensure no two entries overlap in time — each ends before the next starts."""
    if len(entries) <= 1:
        return entries

    for i in range(len(entries) - 1):
        current_end_ms = _timestamp_to_ms(entries[i].end)
        next_start_ms = _timestamp_to_ms(entries[i + 1].start)
        if current_end_ms > next_start_ms:
            # Trim current entry to end just before next starts
            entries[i].end = _ms_to_timestamp(next_start_ms - 1)

    return entries


def _split_text_smartly(text: str, max_len: int) -> list[str]:
    """Split text into chunks at word boundaries, each <= max_len chars."""
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for word in words:
        word_len = len(word) + (1 if current else 0)
        if current_len + word_len > max_len and current:
            chunks.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += word_len

    if current:
        chunks.append(" ".join(current))
    return chunks


def _distribute_timestamps(start: str, end: str, chunks: list[str]) -> list[SrtEntry]:
    """Distribute time evenly across chunks."""
    if len(chunks) <= 1:
        return [SrtEntry(0, start, end, chunks[0])] if chunks else []

    start_ms = _timestamp_to_ms(start)
    end_ms = _timestamp_to_ms(end)
    total_ms = end_ms - start_ms
    chunk_duration = total_ms / len(chunks)

    entries: list[SrtEntry] = []
    for i, chunk in enumerate(chunks):
        chunk_start = start_ms + int(i * chunk_duration)
        chunk_end = start_ms + int((i + 1) * chunk_duration)
        entries.append(SrtEntry(0, _ms_to_timestamp(chunk_start), _ms_to_timestamp(chunk_end), chunk))
    return entries


def _timestamp_to_ms(ts: str) -> int:
    """Convert SRT timestamp (HH:MM:SS,mmm) to milliseconds."""
    ts = ts.replace(",", ".").strip()
    parts = ts.split(":")
    hours = int(parts[0])
    minutes = int(parts[1])
    sec_parts = parts[2].split(".")
    seconds = int(sec_parts[0])
    millis = int(sec_parts[1]) if len(sec_parts) > 1 else 0
    return (hours * 3600 + minutes * 60 + seconds) * 1000 + millis


def _ms_to_timestamp(ms: int) -> str:
    """Convert milliseconds to SRT timestamp (HH:MM:SS,mmm)."""
    hours = ms // 3600000
    ms %= 3600000
    minutes = ms // 60000
    ms %= 60000
    seconds = ms // 1000
    millis = ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _ms_to_vtt_timestamp(ms: int) -> str:
    """Convert milliseconds to VTT timestamp (HH:MM:SS.mmm)."""
    hours = ms // 3600000
    ms %= 3600000
    minutes = ms // 60000
    ms %= 60000
    seconds = ms // 1000
    millis = ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


@dataclass
class SrtEntry:
    """Represents a single SRT subtitle entry."""

    index: int
    start: str
    end: str
    text: str


def _parse_srt(content: str) -> list[SrtEntry]:
    """Parse SRT content into list of entries."""
    entries: list[SrtEntry] = []
    # Split by blank lines (handles \r\n and \n)
    blocks = re.split(r"\n\s*\n", content.strip())

    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue

        # First line: index number
        try:
            index = int(lines[0].strip())
        except ValueError:
            continue

        # Second line: timestamps
        timestamp_line = lines[1].strip()
        if " --> " not in timestamp_line:
            continue
        start, end = timestamp_line.split(" --> ", 1)

        # Remaining lines: subtitle text
        text = "\n".join(lines[2:])
        entries.append(SrtEntry(index, start.strip(), end.strip(), text))

    return entries


def _entries_to_srt(entries: list[SrtEntry]) -> str:
    """Convert list of SRT entries back to SRT format string."""
    lines: list[str] = []
    for i, entry in enumerate(entries, 1):
        lines.append(str(i))
        lines.append(f"{entry.start} --> {entry.end}")
        lines.append(entry.text)
        lines.append("")  # Blank separator
    return "\n".join(lines)


# ─── VTT Export ──────────────────────────────────────────────

def export_to_vtt(srt_path: Path, output_path: Path | None = None) -> Path:
    """Convert an SRT file to WebVTT (VTT) format.

    Args:
        srt_path: Path to the source .srt file.
        output_path: Path for the output .vtt file. If None, uses same name.

    Returns:
        Path to the generated .vtt file.
    """
    if output_path is None:
        output_path = srt_path.with_suffix(".vtt")

    content = srt_path.read_text(encoding="utf-8", errors="replace")
    entries = _parse_srt(content)

    vtt_lines: list[str] = ["WEBVTT", "Kind: captions", ""]

    for i, entry in enumerate(entries, 1):
        # Convert SRT timestamps to VTT timestamps (comma → dot)
        start_ms = _timestamp_to_ms(entry.start)
        end_ms = _timestamp_to_ms(entry.end)
        start_vtt = _ms_to_vtt_timestamp(start_ms)
        end_vtt = _ms_to_vtt_timestamp(end_ms)

        vtt_lines.append(str(i))
        vtt_lines.append(f"{start_vtt} --> {end_vtt}")
        vtt_lines.append(entry.text)
        vtt_lines.append("")

    output_path.write_text("\n".join(vtt_lines), encoding="utf-8")
    logger.info("📝 Exported VTT: %s (%d entries)", output_path.name, len(entries))
    return output_path


# ─── JSON Export ─────────────────────────────────────────────

def export_to_json(srt_path: Path, output_path: Path | None = None) -> Path:
    """Export SRT as structured JSON for programmatic use.

    Args:
        srt_path: Path to the source .srt file.
        output_path: Path for the output .json file. If None, uses same name.

    Returns:
        Path to the generated .json file.
    """
    if output_path is None:
        output_path = srt_path.with_suffix(".json")

    content = srt_path.read_text(encoding="utf-8", errors="replace")
    entries = _parse_srt(content)

    data = {
        "metadata": {
            "source": srt_path.name,
            "entries": len(entries),
            "exported_at": datetime.now().isoformat(),
        },
        "subtitles": [
            {
                "index": i,
                "start": entry.start,
                "end": entry.end,
                "start_ms": _timestamp_to_ms(entry.start),
                "end_ms": _timestamp_to_ms(entry.end),
                "text": entry.text,
            }
            for i, entry in enumerate(entries, 1)
        ],
    }

    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("📊 Exported JSON: %s (%d entries)", output_path.name, len(entries))
    return output_path


def generate_subtitle_filterscript(
    srt_path: Path,
    watermark_filter: str,
    font_name: str = "Arial",
    font_size: int = 28,
    font_color: str = "white",
    box_color: str = "black",
    box_opacity: float = 0.6,
    box_border_w: int = 8,
    margin_v: int = 30,
) -> Path:
    """Generate FFmpeg filter_script with drawtext filters for each subtitle entry.

    Uses the same drawtext+box technique as the Daisy watermark,
    ensuring identical visual style (especially alpha transparency).
    """
    content = srt_path.read_text(encoding="utf-8", errors="replace")
    entries = _parse_srt(content)

    # Start filter chain with watermark
    filters: list[str] = [watermark_filter]

    for entry in entries:
        start_sec = _timestamp_to_ms(entry.start) / 1000.0
        end_sec = _timestamp_to_ms(entry.end) / 1000.0

        # Escape special chars for FFmpeg drawtext filter syntax
        # Order matters: backslash first, then others
        text = entry.text.replace("'", "’")  # Smart quote avoids FFmpeg quoting issues
        text = text.replace("\\", "\\\\")
        text = text.replace(":", "\\:")
        text = text.replace(",", "\\,")
        text = text.replace(";", "\\;")
        text = text.replace("$", "\\$")
        text = text.replace("%", "%%")

        dt = (
            f"drawtext=text='{text}'"
            f":font={font_name}"
            f":fontsize={font_size}"
            f":fontcolor={font_color}"
            f":x=(w-tw)/2"
            f":y=h-th-{margin_v}"
            f":box=1"
            f":boxcolor={box_color}@{box_opacity}"
            f":boxborderw={box_border_w}"
            f":enable='between(t\\,{start_sec:.3f}\\,{end_sec:.3f})'"
        )
        filters.append(dt)

    # Write filter chain to script file (avoids command line length limits)
    script_path = srt_path.with_suffix(".filter")
    filter_chain = ",\n".join(filters)
    script_path.write_text(filter_chain, encoding="utf-8")
    logger.info(
        "Generated drawtext filter script: %s (%d subtitle entries)",
        script_path.name,
        len(entries),
    )
    return script_path
