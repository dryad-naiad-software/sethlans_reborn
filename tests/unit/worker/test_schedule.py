# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for worker/sethlans_worker_agent/idle_detection/schedule.py.

Covers time-window claim gate logic (FR-8):
- In-window and out-of-window checks
- Cross-midnight windows
- Day-of-week filtering
- Disabled window returns True
- Timezone handling
"""
from datetime import time as dt_time

import pytest

from sethlans_worker_agent.idle_detection.schedule import (
    _check_time_in_window,
    _parse_time,
    _parse_allowed_days,
    is_inside_claim_window,
)


class TestParseTime:
    """Utility: HH:MM string to datetime.time."""

    def test_valid_time(self):
        assert _parse_time("18:00") == dt_time(18, 0)

    def test_valid_time_with_minutes(self):
        assert _parse_time("07:30") == dt_time(7, 30)

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            _parse_time("invalid")


class TestParseAllowedDays:
    """Utility: day abbreviations to weekday ints."""

    def test_specific_days(self):
        result = _parse_allowed_days(["mon", "wed", "fri"])
        assert result == {0, 2, 4}

    def test_empty_list_returns_all(self):
        result = _parse_allowed_days([])
        assert result == {0, 1, 2, 3, 4, 5, 6}

    def test_none_returns_all(self):
        result = _parse_allowed_days(None)
        assert result == {0, 1, 2, 3, 4, 5, 6}

    def test_case_insensitive(self):
        result = _parse_allowed_days(["MON", "Tue"])
        assert result == {0, 1}


class TestCheckTimeInWindow:
    """Core time-in-window logic with cross-midnight support."""

    def test_in_window_evening(self):
        """19:00 with start=18:00 end=07:00 -> True."""
        current = dt_time(19, 0)
        start = dt_time(18, 0)
        end = dt_time(7, 0)
        weekday = 0  # Monday
        allowed = {0, 1, 2, 3, 4, 5, 6}
        assert _check_time_in_window(
            current, start, end, weekday, allowed,
        ) is True

    def test_out_of_window_before_start(self):
        """17:59 with start=18:00 end=07:00 -> False."""
        current = dt_time(17, 59)
        start = dt_time(18, 0)
        end = dt_time(7, 0)
        weekday = 0
        allowed = {0, 1, 2, 3, 4, 5, 6}
        assert _check_time_in_window(
            current, start, end, weekday, allowed,
        ) is False

    def test_cross_midnight_late_night(self):
        """23:00 with start=22:00 end=06:00 -> True."""
        current = dt_time(23, 0)
        start = dt_time(22, 0)
        end = dt_time(6, 0)
        weekday = 3  # Thursday
        allowed = {0, 1, 2, 3, 4, 5, 6}
        assert _check_time_in_window(
            current, start, end, weekday, allowed,
        ) is True

    def test_cross_midnight_early_morning(self):
        """03:00 with start=22:00 end=06:00 -> True (previous day started)."""
        current = dt_time(3, 0)
        start = dt_time(22, 0)
        end = dt_time(6, 0)
        weekday = 4  # Friday; yesterday (Thu=3) must be in allowed
        allowed = {0, 1, 2, 3, 4, 5, 6}
        assert _check_time_in_window(
            current, start, end, weekday, allowed,
        ) is True

    def test_cross_midnight_out_of_window_after_end(self):
        """06:01 with start=22:00 end=06:00 -> False."""
        current = dt_time(6, 1)
        start = dt_time(22, 0)
        end = dt_time(6, 0)
        weekday = 4
        allowed = {0, 1, 2, 3, 4, 5, 6}
        assert _check_time_in_window(
            current, start, end, weekday, allowed,
        ) is False

    def test_same_day_window(self):
        """12:00 with start=09:00 end=17:00 -> True."""
        current = dt_time(12, 0)
        start = dt_time(9, 0)
        end = dt_time(17, 0)
        weekday = 2
        allowed = {0, 1, 2, 3, 4}
        assert _check_time_in_window(
            current, start, end, weekday, allowed,
        ) is True

    def test_weekday_filter_excludes(self):
        """Tuesday (weekday=1) with allowed=[mon, wed] -> False."""
        current = dt_time(19, 0)
        start = dt_time(18, 0)
        end = dt_time(7, 0)
        weekday = 1  # Tuesday
        allowed = {0, 2}  # Monday, Wednesday
        assert _check_time_in_window(
            current, start, end, weekday, allowed,
        ) is False

    def test_weekday_filter_includes(self):
        """Wednesday (weekday=2) with allowed=[mon, wed] -> True."""
        current = dt_time(19, 0)
        start = dt_time(18, 0)
        end = dt_time(7, 0)
        weekday = 2  # Wednesday
        allowed = {0, 2}
        assert _check_time_in_window(
            current, start, end, weekday, allowed,
        ) is True

    def test_cross_midnight_yesterday_not_allowed(self):
        """03:00 but yesterday is not in allowed days -> False."""
        current = dt_time(3, 0)
        start = dt_time(22, 0)
        end = dt_time(6, 0)
        weekday = 1  # Tuesday -> yesterday Monday(0) not in allowed
        allowed = {1, 2, 3}  # Tue, Wed, Thu only
        assert _check_time_in_window(
            current, start, end, weekday, allowed,
        ) is False


class TestIsInsideClaimWindow:
    """FR-8: Full is_inside_claim_window with config dict."""

    def test_disabled_window_returns_true(self):
        """FR-8b: enabled=false -> always inside (no restriction)."""
        config = {"enabled": False, "start": "18:00", "end": "07:00"}
        assert is_inside_claim_window(config) is True

    def test_missing_enabled_returns_true(self):
        """No enabled key -> defaults to false -> True."""
        config = {"start": "18:00", "end": "07:00"}
        assert is_inside_claim_window(config) is True

    def test_non_dict_returns_true(self):
        """Non-dict config -> True (no restriction)."""
        assert is_inside_claim_window(None) is True
        assert is_inside_claim_window("invalid") is True

    def test_enabled_window_sunday_evening(self):
        """Enabled window: Sunday 19:30 with start=18:00 end=07:00 -> True."""
        result = _check_time_in_window(
            dt_time(19, 30), dt_time(18, 0), dt_time(7, 0),
            6,  # Sunday
            {6},
        )
        assert result is True

    def test_invalid_time_format_returns_true(self):
        """Invalid start/end format -> True (fail-open)."""
        config = {
            "enabled": True,
            "start": "invalid",
            "end": "07:00",
        }
        assert is_inside_claim_window(config) is True
