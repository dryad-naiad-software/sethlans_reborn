# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The pure sequence planner (Unreal sequence spec FR-SP-1..7, OQ-1 (a)).  No
database access.

* Shot parts: the chosen shots (or all), clipped to the frame range.
* Runs: maximal lists of parts where each starts at the previous one's end.
* Pieces, per run, ``T = max(1, round(piece_seconds * num / den))`` frames:
  a part longer than ``T + T // 2`` splits into ``ceil(n / T)`` sub-parts
  differing by at most one frame; other parts are grouped greedily.
* Lead-in (OQ-1 (a)): every piece gets ``min(L, start - covered_start)``
  frames, where ``covered_start`` is the first frame of the gap-free
  stretch of **all** discovered shots holding the start.  So a piece right
  after a gap or at the playback start gets none.
* Audio runs (FR-SP-5): one per run, rendered from ``r0``.

The pilot's frames are ``sequence_pilot``.  Frames are edit frames; every
``end`` is exclusive.
"""

import math
from bisect import bisect_right
from dataclasses import dataclass, field

from ..constants_sequence import MAX_PIECES, MAX_SUBMISSION_SHOTS

# MRG skips its latency compensation for a first segment under 8 frames (FR-SP-5).
AUDIO_MIN_SEGMENT = 8


class PlanError(ValueError):
    """The selection cannot be planned (the submission becomes ERROR with this text)."""


@dataclass(frozen=True)
class Shot:
    index: int
    name: str
    start: int
    end: int


@dataclass(frozen=True)
class Selection:
    """FR-SQ-3: shot names, or an inclusive frame range, or neither (the whole sequence)."""
    shots: frozenset = None
    frame_start: int = None
    frame_end: int = None


@dataclass
class Piece:
    start: int
    end: int
    parts: tuple
    lead: int = 0

    def piece_shots(self):
        """``[[index, start, end], ...]`` of the parts, clipped to the piece (end exclusive)."""
        return [[p.index, max(p.start, self.start), min(p.end, self.end)] for p in self.parts]


@dataclass(frozen=True)
class AudioRun:
    start: int
    end: int
    r0: int


@dataclass(frozen=True)
class Plan:
    parts: tuple
    runs: tuple
    pieces: tuple
    audio: tuple = field(default=())


class Coverage:
    """The discovered shots and their gap-free stretches (lookups by bisection)."""

    def __init__(self, shots):
        self.shots = sorted(shots, key=lambda s: s.start)
        self._starts = [s.start for s in self.shots]
        stretches = []
        for shot in self.shots:
            if stretches and stretches[-1][1] == shot.start:
                stretches[-1][1] = shot.end
            else:
                stretches.append([shot.start, shot.end])
        self._stretches = stretches
        self._stretch_starts = [s for s, _e in stretches]

    def covered_start(self, frame):
        """The first frame of the stretch holding ``frame`` (``frame`` itself in a gap)."""
        i = bisect_right(self._stretch_starts, frame) - 1
        if i >= 0 and frame < self._stretches[i][1]:
            return self._stretches[i][0]
        return frame

    def shot_at(self, frame):
        i = bisect_right(self._starts, frame) - 1
        if i >= 0 and frame < self.shots[i].end:
            return self.shots[i]
        return None

    def overlapping(self, lo, hi):
        """Shots that share a frame with ``[lo, hi)``."""
        i = max(0, bisect_right(self._starts, lo) - 1)
        found = []
        while i < len(self.shots) and self.shots[i].start < hi:
            if self.shots[i].end > lo:
                found.append(self.shots[i])
            i += 1
        return found


def shots_from(rows):
    """``Shot`` objects from a discovery's ``shots`` JSON (``{"index", "name", "start", "end"}``)."""
    return [Shot(int(r['index']), str(r['name']), int(r['start']), int(r['end'])) for r in rows]


def choose_parts(shots, selection):
    """FR-SP-2: the chosen shots, clipped to the frame range, in edit order."""
    lo, hi = None, None
    if selection.frame_start is not None:
        lo, hi = selection.frame_start, selection.frame_end + 1
    parts = []
    for shot in sorted(shots, key=lambda s: s.start):
        if selection.shots is not None and shot.name not in selection.shots:
            continue
        start = shot.start if lo is None else max(shot.start, lo)
        end = shot.end if hi is None else min(shot.end, hi)
        if end > start:
            parts.append(Shot(shot.index, shot.name, start, end))
    return parts


def split_runs(parts):
    runs = []
    for part in parts:
        if runs and runs[-1][-1].end == part.start:
            runs[-1].append(part)
        else:
            runs.append([part])
    return runs


def even_split(start, end, count):
    """``count`` contiguous ranges over ``[start, end)`` whose lengths differ by at most one."""
    base, extra = divmod(end - start, count)
    ranges, cursor = [], start
    for i in range(count):
        size = base + (1 if i < extra else 0)
        ranges.append((cursor, cursor + size))
        cursor += size
    return ranges


def _close(group, pieces):
    if group:
        pieces.append(Piece(group[0].start, group[-1].end, tuple(group)))


def _run_pieces(run, target, limit, pieces):
    group, frames = [], 0
    for part in run:
        n = part.end - part.start
        if n > limit:
            _close(group, pieces)
            group, frames = [], 0
            for start, end in even_split(part.start, part.end, math.ceil(n / target)):
                pieces.append(Piece(start, end, (part,)))
        elif not group or (frames < target and frames + n <= limit):
            group.append(part)
            frames += n
        else:
            _close(group, pieces)
            group, frames = [part], n
    _close(group, pieces)


def audio_r0(coverage, start, lead_frames):
    """FR-SP-5: where the audio job of a run starting at ``start`` begins rendering."""
    lo = coverage.covered_start(start)
    r0 = start - min(max(lead_frames, AUDIO_MIN_SEGMENT), start - lo)
    while r0 > lo:
        shot = coverage.shot_at(r0)
        if shot is None or shot.end - r0 >= AUDIO_MIN_SEGMENT:
            break
        r0 = max(lo, shot.end - AUDIO_MIN_SEGMENT)
    return r0


def plan(shots, selection, rate, piece_seconds, lead_in_ms, playback_start=0):
    """FR-SP-1: parts, runs, pieces and audio runs for one submission.

    Raises:
        PlanError: nothing to render, or above the FR-SP-7 limits.
    """
    num, den = rate
    coverage = Coverage(shots)
    parts = choose_parts(coverage.shots, selection)
    if not parts:
        raise PlanError("No frame of the selection lies inside a shot.")
    if len(parts) > MAX_SUBMISSION_SHOTS:
        raise PlanError(f"The submission has {len(parts)} shots; at most {MAX_SUBMISSION_SHOTS} are allowed.")
    runs = split_runs(parts)
    target = max(1, round(piece_seconds * num / den))
    lead = round(lead_in_ms / 1000 * num / den)
    pieces = []
    for run in runs:
        _run_pieces(run, target, target + target // 2, pieces)
    if len(pieces) > MAX_PIECES:
        raise PlanError(f"The submission needs {len(pieces)} pieces; at most {MAX_PIECES} are allowed.")
    for piece in pieces:
        floor = max(playback_start, coverage.covered_start(piece.start))
        piece.lead = max(0, min(lead, piece.start - floor))
    audio = tuple(AudioRun(run[0].start, run[-1].end, audio_r0(coverage, run[0].start, lead)) for run in runs)
    return Plan(tuple(parts), tuple(tuple(run) for run in runs), tuple(pieces), audio)
