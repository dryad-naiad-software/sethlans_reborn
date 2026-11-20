# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
What a sequence submission's pilot measures (Unreal sequence spec FR-SR-1,
OQ-2 (b), D8).  Pure, no database access.

The pilot measures the chosen shots plus the unchosen shots the lead-ins
render (their memory counts too): the first frame of each chosen shot
part, and the first frame of each unchosen shot a piece's lead-in (or,
with sound, an audio lead-in) reaches into.  Above 256 frames, 256 are
taken evenly spaced over the list, the first and last always included;
the sampled peak is then not an upper bound, and the caller adds a
warning.

The key's shot set (:func:`measured_ranges`) is that same measured set:
the chosen parts plus the reached shots.  Two submissions whose lead-ins
or sound reach different shots therefore get different keys and never
share a pilot that did not measure what they render.
"""

from ..constants_sequence import MAX_PILOT_FRAMES
from .sequence_plan import Coverage


def sample_evenly(values, count):
    """At most ``count`` values evenly spread over ``values``, the first and last always included."""
    if len(values) <= count:
        return list(values)
    last = len(values) - 1
    return [values[round(i * last / (count - 1))] for i in range(count)]


def reached_shots(result, shots, sound):
    """The unchosen shots a piece's lead-in (or, with ``sound``, an audio lead-in) reaches into."""
    coverage = Coverage(shots)
    chosen = {part.index for part in result.parts}
    reaches = [(piece.start - piece.lead, piece.start) for piece in result.pieces if piece.lead]
    if sound:
        reaches += [(run.r0, run.start) for run in result.audio if run.r0 < run.start]
    found = {}
    for low, high in reaches:
        for shot in coverage.overlapping(low, high):
            if shot.index not in chosen:
                found[shot.index] = shot
    return sorted(found.values(), key=lambda shot: shot.start)


def measured_ranges(result, shots, sound):
    """D8, OQ-2 (b): ``[start, end)`` of the chosen parts and the reached shots (the key's shot set)."""
    return [(part.start, part.end) for part in result.parts] + [
        (shot.start, shot.end) for shot in reached_shots(result, shots, sound)
    ]


def pilot_frames(result, shots, sound):
    """``(frames, total)`` for a ``sequence_plan.Plan``: ``frames`` holds at most 256 of ``total``."""
    frames = {part.start for part in result.parts}
    frames.update(shot.start for shot in reached_shots(result, shots, sound))
    ordered = sorted(frames)
    return sample_evenly(ordered, MAX_PILOT_FRAMES), len(ordered)
