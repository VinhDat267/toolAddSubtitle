"""Subtitle quality analysis and scoring."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from subtitle_tool.srt_utils import SrtEntry, _parse_srt, _timestamp_to_ms

logger = logging.getLogger(__name__)


# ─── Quality thresholds ──────────────────────────────────────

# Characters Per Second — reading speed
CPS_MIN = 5      # Too slow → likely bad timing
CPS_IDEAL_MIN = 10
CPS_IDEAL_MAX = 25
CPS_MAX = 35     # Too fast → unreadable

# Duration per entry (seconds)
DURATION_MIN = 0.5    # Flicker — too short to read
DURATION_MAX = 8.0    # Should be split into shorter entries

# Gap between entries (seconds)
GAP_WARNING = 5.0     # Potential missing content

# Line length
MAX_LINE_LENGTH = 45


# ─── Data models ─────────────────────────────────────────────


@dataclass
class QualityIssue:
    """A single quality issue found in the subtitles."""

    severity: str       # "error", "warning", "info"
    category: str       # "timing", "content", "format"
    entry_index: int    # 0 = global issue
    message: str

    def __str__(self) -> str:
        icons = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}
        icon = icons.get(self.severity, "•")
        prefix = f"#{self.entry_index}" if self.entry_index > 0 else "Global"
        return f"  {icon} [{prefix}] {self.message}"


@dataclass
class QualityReport:
    """Complete quality analysis report."""

    total_entries: int = 0
    total_duration_sec: float = 0
    total_chars: int = 0

    # Timing stats
    avg_cps: float = 0
    min_cps: float = 0
    max_cps: float = 0
    entries_too_fast: int = 0       # CPS > CPS_MAX
    entries_too_slow: int = 0       # CPS < CPS_MIN

    # Duration stats
    avg_duration: float = 0
    entries_too_short: int = 0      # < DURATION_MIN
    entries_too_long: int = 0       # > DURATION_MAX

    # Format stats
    entries_too_wide: int = 0       # > MAX_LINE_LENGTH chars
    entries_empty: int = 0
    overlaps: int = 0
    large_gaps: int = 0

    # Content stats
    artifacts_found: int = 0        # [music], >>, etc still present

    # Issues list
    issues: list[QualityIssue] = field(default_factory=list)

    # Final score
    score: float = 0                # 0-100

    @property
    def grade(self) -> str:
        """Letter grade from score."""
        if self.score >= 90:
            return "A"
        if self.score >= 80:
            return "B"
        if self.score >= 70:
            return "C"
        if self.score >= 60:
            return "D"
        return "F"

    @property
    def grade_emoji(self) -> str:
        emojis = {"A": "🌟", "B": "✅", "C": "⚠️", "D": "🟡", "F": "❌"}
        return emojis.get(self.grade, "❓")

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            f"{'═' * 55}",
            f"  📊 SUBTITLE QUALITY REPORT",
            f"{'═' * 55}",
            f"",
            f"  Score: {self.score:.0f}/100 {self.grade_emoji} Grade {self.grade}",
            f"",
            f"  ┌─ Overview ──────────────────────────────────",
            f"  │ Entries:        {self.total_entries}",
            f"  │ Total duration: {self.total_duration_sec / 60:.1f} min",
            f"  │ Total chars:    {self.total_chars:,}",
            f"  │ Avg CPS:        {self.avg_cps:.1f} chars/sec"
            f"  (ideal: {CPS_IDEAL_MIN}-{CPS_IDEAL_MAX})",
            f"  │ Avg duration:   {self.avg_duration:.1f}s per entry",
            f"  └──────────────────────────────────────────────",
            f"",
        ]

        # Timing section
        lines.append("  ┌─ Timing ────────────────────────────────────")
        if self.entries_too_fast:
            lines.append(f"  │ ⚠️  {self.entries_too_fast} entries too fast"
                         f" (>{CPS_MAX} CPS)")
        if self.entries_too_slow:
            lines.append(f"  │ ⚠️  {self.entries_too_slow} entries too slow"
                         f" (<{CPS_MIN} CPS)")
        if self.entries_too_short:
            lines.append(f"  │ ❌ {self.entries_too_short} entries too short"
                         f" (<{DURATION_MIN}s)")
        if self.entries_too_long:
            lines.append(f"  │ ⚠️  {self.entries_too_long} entries too long"
                         f" (>{DURATION_MAX}s)")
        if self.overlaps:
            lines.append(f"  │ ❌ {self.overlaps} timestamp overlaps")
        if self.large_gaps:
            lines.append(f"  │ ℹ️  {self.large_gaps} large gaps"
                         f" (>{GAP_WARNING}s)")
        if not any([self.entries_too_fast, self.entries_too_slow,
                     self.entries_too_short, self.entries_too_long,
                     self.overlaps]):
            lines.append("  │ ✅ All timing OK")
        lines.append("  └──────────────────────────────────────────────")
        lines.append("")

        # Format section
        lines.append("  ┌─ Format ────────────────────────────────────")
        if self.entries_too_wide:
            lines.append(f"  │ ⚠️  {self.entries_too_wide} entries >"
                         f" {MAX_LINE_LENGTH} chars")
        if self.entries_empty:
            lines.append(f"  │ ❌ {self.entries_empty} empty entries")
        if self.artifacts_found:
            lines.append(f"  │ ⚠️  {self.artifacts_found} artifacts remaining"
                         f" ([music], >> etc)")
        if not any([self.entries_too_wide, self.entries_empty,
                     self.artifacts_found]):
            lines.append("  │ ✅ All formatting OK")
        lines.append("  └──────────────────────────────────────────────")

        # Top issues (max 5)
        errors = [i for i in self.issues if i.severity == "error"]
        warnings = [i for i in self.issues if i.severity == "warning"]
        if errors or warnings:
            lines.append("")
            lines.append(f"  ┌─ Top Issues ({len(errors)} errors,"
                         f" {len(warnings)} warnings) ─────")
            for issue in (errors + warnings)[:8]:
                lines.append(f"  │ {issue}")
            if len(errors) + len(warnings) > 8:
                lines.append(f"  │ ... and"
                             f" {len(errors) + len(warnings) - 8} more")
            lines.append("  └──────────────────────────────────────────────")

        lines.append(f"{'═' * 55}")
        return "\n".join(lines)


# ─── Analysis engine ─────────────────────────────────────────


def analyze_srt(srt_path: Path) -> QualityReport:
    """Analyze an SRT file and return a quality report.

    Args:
        srt_path: Path to the .srt file to analyze.

    Returns:
        QualityReport with scores, stats, and issues.
    """
    content = srt_path.read_text(encoding="utf-8", errors="replace")
    entries = _parse_srt(content)

    report = QualityReport(total_entries=len(entries))

    if not entries:
        report.issues.append(QualityIssue(
            "error", "content", 0, "SRT file is empty — no entries found."
        ))
        report.score = 0
        return report

    cps_values: list[float] = []
    durations: list[float] = []

    for i, entry in enumerate(entries, 1):
        start_ms = _timestamp_to_ms(entry.start)
        end_ms = _timestamp_to_ms(entry.end)
        duration_sec = (end_ms - start_ms) / 1000.0
        text = entry.text.strip()
        char_count = len(text)

        report.total_chars += char_count

        # ── Empty entry ──
        if not text:
            report.entries_empty += 1
            report.issues.append(QualityIssue(
                "error", "content", i, "Empty subtitle entry."
            ))
            continue

        # ── Duration check ──
        durations.append(duration_sec)

        if duration_sec < DURATION_MIN:
            report.entries_too_short += 1
            report.issues.append(QualityIssue(
                "error", "timing", i,
                f"Too short ({duration_sec:.2f}s): \"{text[:30]}...\""
                if len(text) > 30 else
                f"Too short ({duration_sec:.2f}s): \"{text}\""
            ))

        if duration_sec > DURATION_MAX:
            report.entries_too_long += 1
            report.issues.append(QualityIssue(
                "warning", "timing", i,
                f"Too long ({duration_sec:.1f}s): \"{text[:30]}...\""
                if len(text) > 30 else
                f"Too long ({duration_sec:.1f}s): \"{text}\""
            ))

        # ── CPS (reading speed) ──
        if duration_sec > 0:
            cps = char_count / duration_sec
            cps_values.append(cps)

            if cps > CPS_MAX:
                report.entries_too_fast += 1
                report.issues.append(QualityIssue(
                    "warning", "timing", i,
                    f"Too fast ({cps:.0f} CPS): \"{text[:30]}...\""
                    if len(text) > 30 else
                    f"Too fast ({cps:.0f} CPS): \"{text}\""
                ))
            elif cps < CPS_MIN:
                report.entries_too_slow += 1

        # ── Line length ──
        if char_count > MAX_LINE_LENGTH:
            report.entries_too_wide += 1
            report.issues.append(QualityIssue(
                "warning", "format", i,
                f"Too wide ({char_count} chars): \"{text[:35]}...\""
            ))

        # ── Artifacts ──
        if re.search(r"\[.*?\]", text):
            report.artifacts_found += 1
            report.issues.append(QualityIssue(
                "warning", "content", i,
                f"Artifact remaining: \"{text[:40]}\""
            ))
        if text.startswith(">>"):
            report.artifacts_found += 1
            report.issues.append(QualityIssue(
                "warning", "content", i,
                f"YouTube marker: \"{text[:40]}\""
            ))

    # ── Overlap & gap check ──
    for i in range(len(entries) - 1):
        current_end = _timestamp_to_ms(entries[i].end)
        next_start = _timestamp_to_ms(entries[i + 1].start)

        if current_end > next_start:
            report.overlaps += 1
            overlap_ms = current_end - next_start
            report.issues.append(QualityIssue(
                "error", "timing", i + 1,
                f"Overlaps with next entry by {overlap_ms}ms."
            ))

        gap_sec = (next_start - current_end) / 1000.0
        if gap_sec > GAP_WARNING:
            report.large_gaps += 1

    # ── Compute stats ──
    if cps_values:
        report.avg_cps = sum(cps_values) / len(cps_values)
        report.min_cps = min(cps_values)
        report.max_cps = max(cps_values)

    if durations:
        report.avg_duration = sum(durations) / len(durations)
        report.total_duration_sec = sum(durations)

    # ── Calculate score (100 points) ──
    report.score = _calculate_score(report)

    return report


def _calculate_score(r: QualityReport) -> float:
    """Calculate quality score from 0 to 100.

    Scoring breakdown:
      - Timing:   40 points (CPS, duration, overlaps)
      - Format:   30 points (line length, empty, artifacts)
      - Coverage: 30 points (entry count, gaps)
    """
    if r.total_entries == 0:
        return 0

    n = r.total_entries
    score = 100.0

    # ── Timing penalties (40 points max) ──
    # Overlaps: -5 each (critical)
    score -= min(r.overlaps * 5, 20)
    # Too short: -3 each
    score -= min(r.entries_too_short * 3, 15)
    # Too fast: -1 each
    score -= min(r.entries_too_fast * 1, 10)
    # Too slow: -0.5 each (less severe)
    score -= min(r.entries_too_slow * 0.5, 5)
    # Too long: -0.5 each
    score -= min(r.entries_too_long * 0.5, 5)

    # ── Format penalties (30 points max) ──
    # Empty entries: -5 each
    score -= min(r.entries_empty * 5, 15)
    # Too wide: -1 each
    score -= min(r.entries_too_wide * 1, 10)
    # Artifacts: -2 each
    score -= min(r.artifacts_found * 2, 10)

    # ── Coverage bonus/penalty (30 points context) ──
    # Average CPS in ideal range: no penalty
    if r.avg_cps < CPS_IDEAL_MIN:
        score -= 5
    elif r.avg_cps > CPS_IDEAL_MAX:
        score -= 5

    # Too many large gaps → might be missing content
    gap_ratio = r.large_gaps / n if n > 0 else 0
    if gap_ratio > 0.1:
        score -= 10
    elif gap_ratio > 0.05:
        score -= 5

    return max(0, min(100, score))


def check_quality(srt_path: Path, log_output: bool = True) -> QualityReport:
    """Analyze SRT quality and optionally log the report.

    This is the main entry point for quality checking.

    Args:
        srt_path: Path to the .srt file.
        log_output: If True, log the full report to logger.

    Returns:
        QualityReport with all stats and issues.
    """
    report = analyze_srt(srt_path)

    if log_output:
        for line in report.summary().split("\n"):
            logger.info(line)

    return report
