# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Scheduling time-window gate (FR-8).

Determines whether the current wall-clock time falls inside the
configured claim window, respecting day-of-week filtering, cross-
midnight windows, and IANA timezone specifications.
"""
import logging
from datetime import datetime, time as dt_time
from typing import Dict, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

# Lowercase 3-letter day abbreviations to Python weekday int (Monday=0).
_DAY_MAP = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3,
    "fri": 4, "sat": 5, "sun": 6,
}

_ALL_DAYS = list(_DAY_MAP.keys())


def _parse_time(time_str: str) -> dt_time:
    """Parse an HH:MM string to a datetime.time object."""
    parts = time_str.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format: {time_str!r}")
    return dt_time(int(parts[0]), int(parts[1]))


def _get_timezone(tz_name: str) -> ZoneInfo:
    """Resolve a timezone string. 'local' returns the system timezone."""
    if not tz_name or tz_name.lower() == "local":
        # Use the system local timezone by constructing from
        # the local UTC offset. This respects DST at call time.
        return datetime.now().astimezone().tzinfo
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        logger.warning(
            "Unknown timezone %r, falling back to system local.", tz_name
        )
        return datetime.now().astimezone().tzinfo


def _parse_allowed_days(days_cfg):
    """Parse day-of-week config into a set of weekday ints."""
    if not days_cfg:
        return set(_DAY_MAP.values())
    allowed = set()
    for d in days_cfg:
        day_int = _DAY_MAP.get(str(d).lower().strip())
        if day_int is not None:
            allowed.add(day_int)
    return allowed if allowed else set(_DAY_MAP.values())


def _check_time_in_window(current_time, start, end, weekday, allowed_days):
    """Check if current_time is inside the start-end window."""
    if start <= end:
        # Same-day window: e.g. 09:00 - 17:00
        return weekday in allowed_days and start <= current_time <= end
    # Cross-midnight window: e.g. 18:00 - 07:00
    if current_time >= start:
        return weekday in allowed_days
    if current_time <= end:
        yesterday = (weekday - 1) % 7
        return yesterday in allowed_days
    return False


def is_inside_claim_window(window_config: Dict[str, Any]) -> bool:
    """Check if the current time falls inside the configured claim window.

    Handles cross-midnight windows, day-of-week filtering, timezone via
    zoneinfo.ZoneInfo, and DST transitions.

    Returns True if no window is configured (enabled=false).
    """
    if not isinstance(window_config, dict):
        return True
    if not window_config.get("enabled", False):
        return True

    try:
        start = _parse_time(window_config.get("start", "00:00"))
        end = _parse_time(window_config.get("end", "23:59"))
    except (ValueError, TypeError) as exc:
        logger.warning("Invalid claim_window time config: %s", exc)
        return True

    tz = _get_timezone(window_config.get("timezone", "local"))
    now = datetime.now(tz=tz)
    allowed_days = _parse_allowed_days(window_config.get("days"))

    return _check_time_in_window(
        now.time(), start, end, now.weekday(), allowed_days,
    )
