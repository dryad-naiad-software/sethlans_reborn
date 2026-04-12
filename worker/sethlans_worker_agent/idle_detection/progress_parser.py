# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Blender stdout progress extraction (FR-6a).

Parses render progress from Blender's stdout output for the grace
period decision logic. Only the Cycles ``Sample N/M`` pattern is
recognized as a reliable progress indicator.
"""
import re
from typing import Optional

# Matches "Sample N/M" where N and M are positive integers.
# This is the ONLY reliable progress indicator for Cycles renders.
# Do NOT parse Fra:...Remaining: (unreliable time estimates).
# Do NOT parse Rendered N/M Tiles (conflates with Sethlans tiling grid).
_SAMPLE_PATTERN = re.compile(r"Sample\s+(\d+)/(\d+)")


def parse_blender_progress(
    output_line: str,
    render_engine: str,
) -> Optional[float]:
    """Extract render progress (0.0-1.0) from a Blender stdout line.

    For Cycles renders (render_engine == 'CYCLES'):
    - Recognizes ONLY "Sample N/M" pattern. Progress = N/M.
    - The Fra:/Remaining: pattern is NOT used (unreliable: time
      estimates fluctuate and reset during compositing passes).
    - The "Rendered N/M Tiles" pattern is NOT used (conflates
      Blender's internal tile subdivision with Sethlans tiling grid).

    For Eevee and Workbench renders:
    - Returns None (progress is indeterminate for these engines).
    - The grace period logic treats None as "allow to finish"
      (these renders are fast, typically under 30 seconds).

    Args:
        output_line: A single line of Blender's stdout output.
        render_engine: 'CYCLES', 'BLENDER_EEVEE_NEXT', or 'WORKBENCH'.

    Returns:
        Float 0.0-1.0 for Cycles with a valid Sample line, else None.
    """
    if not output_line or render_engine != "CYCLES":
        return None

    match = _SAMPLE_PATTERN.search(output_line)
    if not match:
        return None

    current = int(match.group(1))
    total = int(match.group(2))
    if total <= 0:
        return None

    return min(current / total, 1.0)
