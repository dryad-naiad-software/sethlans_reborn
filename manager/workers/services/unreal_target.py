# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The render target of an Unreal measurement key (Unreal foundation
FR-PM-1, D2): the engine build and the map, sequence and preset a pilot
renders. ``services.unreal_pilot`` re-exports :class:`Target`.
"""

from dataclasses import dataclass

from shared.unreal.validation import target_sha256

from ..renderers.unreal_settings import MAP, PRESET, SEQUENCE


@dataclass(frozen=True)
class Target:
    """What a pilot renders: the job's build and its map, sequence and preset."""

    engine_build: str
    map: str
    sequence: str
    preset: str

    @classmethod
    def of(cls, job):
        settings = job.render_settings if isinstance(job.render_settings, dict) else {}
        return cls(job.engine_build, settings.get(MAP, ''), settings.get(SEQUENCE, ''),
                   settings.get(PRESET, ''))

    def sha256(self):
        return target_sha256(self.engine_build, self.map, self.sequence, self.preset)
