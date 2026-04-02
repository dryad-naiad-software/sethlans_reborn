# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the render_time_parser utility.

Tests parsing of Blender's stdout output to extract render time.
"""
from sethlans_worker_agent.utils.render_time_parser import parse_render_time


class TestParseRenderTime:

    def test_parses_minutes_and_seconds(self):
        # Blender format: "Time: HH:MM:SS.ss (Saving: path)"
        output = (
            "Fra:1 Mem:100M | Time: 00:01:23.45 | Remaining:N/A\n"
            "Fra:1 Mem:100M | Time: 00:02:15.50 (Saving: /tmp/out.png)\n"
        )
        assert parse_render_time(output) == 136  # ceil(135.5)

    def test_parses_hours_minutes_seconds(self):
        output = (
            "Fra:1 Mem:100M | Time: 01:30:00.00 (Saving: /tmp/out.png)\n"
        )
        assert parse_render_time(output) == 5400  # 1h 30m

    def test_parses_zero_hours(self):
        output = (
            "Fra:1 | Time: 00:00:05.10 (Saving: file.png)\n"
        )
        assert parse_render_time(output) == 6  # ceil(5.10)

    def test_parses_fractional_seconds_rounds_up(self):
        output = (
            "Fra:1 | Time: 00:00:00.01 (Saving: out.png)\n"
        )
        assert parse_render_time(output) == 1  # ceil(0.01)

    def test_exact_integer_seconds(self):
        output = (
            "Fra:1 | Time: 00:01:00.00 (Saving: out.png)\n"
        )
        assert parse_render_time(output) == 60

    def test_returns_none_for_missing_saving_line(self):
        output = (
            "Fra:1 Mem:100M | Time: 00:01:23.45 | Remaining:10s\n"
            "Blender quit\n"
        )
        assert parse_render_time(output) is None

    def test_returns_none_for_empty_output(self):
        assert parse_render_time("") is None

    def test_returns_none_for_no_time_in_saving_line(self):
        output = "Some random text (Saving: file.png)\n"
        assert parse_render_time(output) is None

    def test_ignores_non_saving_time_lines(self):
        # Only the (Saving:) line should be used
        output = (
            "Time: 00:99:99.99 | some intermediate line\n"
            "Fra:1 | Time: 00:00:10.00 (Saving: out.png)\n"
        )
        assert parse_render_time(output) == 10

    def test_without_hours_component(self):
        # The regex supports optional hours group: (?:(\d{2}):)?
        output = (
            "Fra:1 | Time: 05:30.25 (Saving: out.png)\n"
        )
        assert parse_render_time(output) == 331  # ceil(330.25)

    def test_large_render_time(self):
        output = (
            "Fra:1 | Time: 23:59:59.99 (Saving: out.png)\n"
        )
        expected = 86400  # ceil(23*3600 + 59*60 + 59.99)
        assert parse_render_time(output) == expected
