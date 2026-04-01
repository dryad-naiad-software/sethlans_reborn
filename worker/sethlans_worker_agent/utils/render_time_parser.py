# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Utility for parsing render time from Blender stdout output.
"""
import logging
import math
import re

logger = logging.getLogger(__name__)

_TIME_LINE_REGEX = re.compile(r"Time: (?:(\d{2}):)?(\d{2}):(\d{2}\.\d{2})")


def parse_render_time(stdout_text):
    """
    Parses Blender's stdout log content to find the total render time by
    finding the unique final summary line containing "(Saving:)".

    Args:
        stdout_text (str): The full stdout log from the Blender subprocess.

    Returns:
        int or None: The total render time in seconds, rounded up to the nearest
                     whole number, or None if the time could not be parsed.
    """
    for line in stdout_text.splitlines():
        if "(Saving:" in line:
            match = _TIME_LINE_REGEX.search(line)
            if match:
                try:
                    hours_str, minutes_str, seconds_str = match.groups()
                    hours = int(hours_str) if hours_str else 0
                    minutes = int(minutes_str)
                    seconds = float(seconds_str)
                    total_seconds = int(
                        math.ceil(
                            (hours * 3600) + (minutes * 60) + seconds
                        )
                    )
                    logger.info(
                        "Parsed render time: %d seconds from line: '%s'",
                        total_seconds, line.strip()
                    )
                    return total_seconds
                except (IndexError, ValueError) as e:
                    logger.warning(
                        "Found summary line but failed to parse "
                        "time: '%s' - %s", line.strip(), e
                    )
                    return None
    logger.warning(
        "Could not find the final 'Time: ... (Saving: ...)' "
        "summary line in the render output."
    )
    return None
