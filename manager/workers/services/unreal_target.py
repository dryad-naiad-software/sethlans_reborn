# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The render target of an Unreal measurement key (Unreal foundation
FR-PM-1, D2; sequence D8, FR-SR-1): the engine build and the map, sequence
and preset a pilot renders, plus, for sequence pieces, the output
resolution and the chosen shot set, both read from the job row so every
path that rebuilds the key gets the same hash.  ``services.unreal_pilot``
re-exports :class:`Target`.

Per-machine memory test (OQ-1, D8): the draft values are part of the key
too; :meth:`Target.base_sha256` is the key without shot set and draft (a
covering result's match), and ``measured_ranges`` are the ``[start, end)``
pairs a sequence pilot measures (not part of the key).
"""

from dataclasses import dataclass, field

from shared.unreal.validation import target_sha256

from ..constants_sequence import DRAFT_KEY, PILOT_FRAMES_KEY, RESOLUTION_KEY, SHOT_SET_KEY
from ..renderers.unreal_settings import MAP, PRESET, SEQUENCE


def _resolution(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return tuple(value)
    return None


def _draft(value):
    """A draft object as sorted ``(key, value)`` pairs (hashable), ``()`` for none."""
    return tuple(sorted(value.items())) if isinstance(value, dict) else ()


@dataclass(frozen=True)
class Target:
    """What a pilot renders: the job's build, map, sequence, preset, (D8) size and shot set, and draft.

    ``pilot_frames`` and ``measured_ranges`` (not part of the key) are the
    frames a sequence pilot renders (FR-SR-1) and the ranges it measures
    (D8); empty for a standalone job's pilot.
    """

    engine_build: str
    map: str
    sequence: str
    preset: str
    resolution: tuple = None
    shot_set_sha256: str = ''
    pilot_frames: tuple = field(default=(), compare=False)
    draft: tuple = ()
    measured_ranges: tuple = field(default=(), compare=False)

    @classmethod
    def of(cls, job):
        """The job's target; a piece's pilot frames and measured ranges come from its submission (one query)."""
        settings = job.render_settings if isinstance(job.render_settings, dict) else {}
        frames, ranges = (), ()
        if getattr(job, 'sequence_submission_id', None) is not None:
            from ..models import SequenceSubmission
            stored = SequenceSubmission.objects.filter(pk=job.sequence_submission_id).values_list(
                'pilot_frames', 'measured_ranges').first() or (None, None)
            frames = tuple(stored[0] or ())
            ranges = tuple(tuple(pair) for pair in stored[1] or ())
        return cls(job.engine_build, settings.get(MAP, ''), settings.get(SEQUENCE, ''),
                   settings.get(PRESET, ''), _resolution(settings.get(RESOLUTION_KEY)),
                   settings.get(SHOT_SET_KEY) or '', frames, _draft(settings.get(DRAFT_KEY)), ranges)

    def draft_settings(self):
        """The draft as the ``unreal.draft`` object, or ``None``."""
        return dict(self.draft) if self.draft else None

    def sha256(self):
        return target_sha256(self.engine_build, self.map, self.sequence, self.preset,
                             self.resolution, self.shot_set_sha256 or None, self.draft_settings())

    def base_sha256(self):
        """D8: the key without shot set and draft (what a covering result must equal)."""
        return target_sha256(self.engine_build, self.map, self.sequence, self.preset, self.resolution)

    def rendered_ranges(self):
        """D8: the measured ranges whose start frame the pilot renders, or ``None`` without ranges."""
        if not self.measured_ranges:
            return None
        frames = set(self.pilot_frames)
        return [[start, end] for start, end in self.measured_ranges if start in frames]

    def pilot_settings(self):
        """The pilot's extra ``render_settings``: the key fields (D8) and the frames (FR-SR-1)."""
        extra = {}
        if self.resolution is not None:
            extra[RESOLUTION_KEY] = list(self.resolution)
        if self.shot_set_sha256:
            extra[SHOT_SET_KEY] = self.shot_set_sha256
        if self.pilot_frames:
            extra[PILOT_FRAMES_KEY] = list(self.pilot_frames)
        if self.draft:
            extra[DRAFT_KEY] = self.draft_settings()
        return extra

    def pilot_frame(self, default):
        """The pilot job's ``start_frame``: its first pilot frame, else ``default``."""
        return self.pilot_frames[0] if self.pilot_frames else default
