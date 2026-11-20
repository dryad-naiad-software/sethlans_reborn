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
"""

from dataclasses import dataclass, field

from shared.unreal.validation import target_sha256

from ..constants_sequence import PILOT_FRAMES_KEY, RESOLUTION_KEY, SHOT_SET_KEY
from ..renderers.unreal_settings import MAP, PRESET, SEQUENCE


def _resolution(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return tuple(value)
    return None


@dataclass(frozen=True)
class Target:
    """What a pilot renders: the job's build, map, sequence, preset and (D8) size and shot set.

    ``pilot_frames`` (not part of the key) are the frames a sequence pilot
    renders (FR-SR-1); empty for a standalone job's pilot.
    """

    engine_build: str
    map: str
    sequence: str
    preset: str
    resolution: tuple = None
    shot_set_sha256: str = ''
    pilot_frames: tuple = field(default=(), compare=False)

    @classmethod
    def of(cls, job):
        """The job's target; a sequence piece's pilot frames come from its submission (one query)."""
        settings = job.render_settings if isinstance(job.render_settings, dict) else {}
        frames = ()
        if getattr(job, 'sequence_submission_id', None) is not None:
            from ..models import SequenceSubmission
            stored = SequenceSubmission.objects.filter(pk=job.sequence_submission_id).values_list(
                'pilot_frames', flat=True).first()
            frames = tuple(stored or ())
        return cls(job.engine_build, settings.get(MAP, ''), settings.get(SEQUENCE, ''),
                   settings.get(PRESET, ''), _resolution(settings.get(RESOLUTION_KEY)),
                   settings.get(SHOT_SET_KEY) or '', frames)

    def sha256(self):
        return target_sha256(self.engine_build, self.map, self.sequence, self.preset,
                             self.resolution, self.shot_set_sha256 or None)

    def pilot_settings(self):
        """The pilot's extra ``render_settings``: the key fields (D8) and the frames (FR-SR-1)."""
        extra = {}
        if self.resolution is not None:
            extra[RESOLUTION_KEY] = list(self.resolution)
        if self.shot_set_sha256:
            extra[SHOT_SET_KEY] = self.shot_set_sha256
        if self.pilot_frames:
            extra[PILOT_FRAMES_KEY] = list(self.pilot_frames)
        return extra

    def pilot_frame(self, default):
        """The pilot job's ``start_frame``: its first pilot frame, else ``default``."""
        return self.pilot_frames[0] if self.pilot_frames else default
