# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for worker/sethlans_worker_agent/idle_detection/progress_parser.py.

Covers Blender stdout progress extraction (FR-6a):
- Sample N/M extraction for Cycles renders
- None for Eevee and Workbench renders
- Rejection of Fra:...Remaining: and Rendered N/M Tiles patterns
- Edge cases: zero, max, empty input
"""
import pytest

from sethlans_worker_agent.idle_detection.progress_parser import (
    parse_blender_progress,
)


class TestCyclesSampleExtraction:
    """FR-6a: Parse 'Sample N/M' for Cycles engine."""

    def test_sample_512_of_1024(self):
        """Sample 512/1024 -> 0.5."""
        result = parse_blender_progress("Sample 512/1024", "CYCLES")
        assert result == pytest.approx(0.5)

    def test_sample_256_of_1024(self):
        """Sample 256/1024 -> 0.25."""
        result = parse_blender_progress("Sample 256/1024", "CYCLES")
        assert result == pytest.approx(0.25)

    def test_sample_1024_of_1024(self):
        """Sample 1024/1024 -> 1.0 (complete)."""
        result = parse_blender_progress("Sample 1024/1024", "CYCLES")
        assert result == pytest.approx(1.0)

    def test_sample_0_of_1024(self):
        """Sample 0/1024 -> 0.0."""
        result = parse_blender_progress("Sample 0/1024", "CYCLES")
        assert result == pytest.approx(0.0)

    def test_sample_1_of_1024(self):
        """Sample 1/1024 -> ~0.00098."""
        result = parse_blender_progress("Sample 1/1024", "CYCLES")
        assert result == pytest.approx(1 / 1024)

    def test_sample_in_longer_line(self):
        """Sample pattern embedded in a longer Blender output line."""
        line = "Fra:1 Mem:256.00M | Remaining:00:05.23 | Sample 768/1024"
        result = parse_blender_progress(line, "CYCLES")
        assert result == pytest.approx(768 / 1024)

    def test_sample_with_extra_whitespace(self):
        """'Sample  512/1024' (extra space) still matched by regex \\s+."""
        result = parse_blender_progress("Sample  512/1024", "CYCLES")
        assert result == pytest.approx(0.5)

    def test_clamped_to_1_0(self):
        """Progress is clamped to 1.0 max (min(N/M, 1.0))."""
        # Hypothetical edge case where N > M
        result = parse_blender_progress("Sample 1100/1024", "CYCLES")
        assert result == pytest.approx(1.0)


class TestNonCyclesEngines:
    """FR-6a: Eevee and Workbench return None (indeterminate)."""

    def test_eevee_returns_none(self):
        result = parse_blender_progress(
            "Sample 512/1024", "BLENDER_EEVEE_NEXT",
        )
        assert result is None

    def test_workbench_returns_none(self):
        result = parse_blender_progress("Sample 512/1024", "WORKBENCH")
        assert result is None

    def test_unknown_engine_returns_none(self):
        result = parse_blender_progress("Sample 512/1024", "UNKNOWN")
        assert result is None


class TestRejectedPatterns:
    """FR-6a: Patterns that must NOT be matched."""

    def test_fra_remaining_not_matched(self):
        """Fra:...Remaining: pattern returns None (unreliable)."""
        line = "Fra:1 Mem:256.00M (Peak 260.00M) | Time:00:02.50 | Remaining:00:05.23"
        result = parse_blender_progress(line, "CYCLES")
        assert result is None

    def test_rendered_tiles_not_matched(self):
        """Rendered N/M Tiles pattern returns None (conflates with Sethlans tiling)."""
        line = "Rendered 3/16 Tiles"
        result = parse_blender_progress(line, "CYCLES")
        assert result is None


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_string_returns_none(self):
        assert parse_blender_progress("", "CYCLES") is None

    def test_none_output_line_returns_none(self):
        """Empty/falsy output line -> None."""
        assert parse_blender_progress("", "CYCLES") is None

    def test_no_sample_pattern_returns_none(self):
        line = "Blender 4.1.1 started"
        assert parse_blender_progress(line, "CYCLES") is None

    def test_total_zero_returns_none(self):
        """Sample N/0 -> division by zero guard returns None."""
        result = parse_blender_progress("Sample 5/0", "CYCLES")
        assert result is None
