# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-shot timings of a sequence piece from its frame files (sequence
rendering FR-SW-9, R7). Pure functions; no file access.

Inputs: the kept frames' ``(frame, mtime)`` in frame order, the lead-in
files' modification times, the render launch's wall-clock time and the
piece's shots ``(index, start, end)``.

* A kept frame's time is the difference to the previous kept file's time.
* The first kept frame has no previous kept file: it takes the **median**
  of the other kept frames' times. A piece with one kept frame has no
  other: it takes the difference to the last lead-in file when there is
  one (that is a real frame time); with no lead-in either, its time is the
  whole span from the launch (an upper bound: engine start included) and
  ``startup_s`` is unknown (``None``), with a warning.
* ``startup_s`` = the first **rendered** file's time - launch - the first
  frame's time (with no lead-in that is the first kept file, as R7 says;
  with a lead-in the lead-in's own frames are not counted as start-up).
* ``lead_in_s`` = the last lead-in file's time - launch; ``None`` without
  a lead-in.
* Per shot: ``frames``, ``seconds`` (the sum of its kept frames' times)
  and ``s_per_frame``. Clock steps backwards give 0, never a negative time;
  every value is capped at ``MAX_SECONDS`` (the manager's bound).
"""
from dataclasses import dataclass
from statistics import median
from typing import List, Optional, Sequence, Tuple

MAX_SECONDS = 259_200.0
ONE_FRAME_WARNING = ("The piece kept one frame and rendered no lead-in, so its frame time includes Unreal's "
                     "start-up.")


@dataclass(frozen=True)
class PieceTiming:
    shots: Tuple[dict, ...]          # {"index", "frames", "seconds", "s_per_frame"} in piece order
    startup_s: Optional[float]
    lead_in_s: Optional[float]
    warnings: Tuple[str, ...] = ()


def _seconds(value: float) -> float:
    return round(min(max(float(value), 0.0), MAX_SECONDS), 3)


def frame_times(kept: Sequence[Tuple[int, float]], lead_mtimes: Sequence[float],
                launched_at: float) -> Tuple[List[float], bool]:
    """``(times, exact)``: one time per kept frame; ``exact`` is False for the one-frame, no-lead-in case."""
    if not kept:
        return [], True
    times = [b[1] - a[1] for a, b in zip(kept, kept[1:])]
    if times:
        return [median(times)] + times, True
    if lead_mtimes:
        return [kept[0][1] - lead_mtimes[-1]], True
    return [kept[0][1] - launched_at], False


def piece_timing(kept: Sequence[Tuple[int, float]], lead_mtimes: Sequence[float], launched_at: float,
                 shots: Sequence[Tuple[int, int, int]]) -> PieceTiming:
    """FR-SW-9 for one piece; ``kept`` and ``shots`` cover the same frames (``job_params_sequence``)."""
    times, exact = frame_times(kept, lead_mtimes, launched_at)
    first_file = lead_mtimes[0] if lead_mtimes else (kept[0][1] if kept else launched_at)
    startup = _seconds(first_file - launched_at - times[0]) if exact and times else None
    lead_in = _seconds(lead_mtimes[-1] - launched_at) if lead_mtimes else None
    by_frame = {frame: max(t, 0.0) for (frame, _mtime), t in zip(kept, times)}
    rows = []
    for index, start, end in shots:
        part = [by_frame[f] for f in range(start, end) if f in by_frame]
        seconds = _seconds(sum(part))
        rows.append({'index': index, 'frames': len(part), 'seconds': seconds,
                     's_per_frame': _seconds(seconds / len(part)) if part else 0.0})
    return PieceTiming(shots=tuple(rows), startup_s=startup, lead_in_s=lead_in,
                       warnings=() if exact else (ONE_FRAME_WARNING,))
