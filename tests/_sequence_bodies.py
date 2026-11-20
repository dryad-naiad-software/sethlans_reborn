# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Made-up shot discovery results for the Unreal sequence tests (FR-SD-5).

Not a test module.  The sequence and shot names are invented.
"""

from __future__ import annotations

import copy

PRESET_FACTS = {
    'resolution': [2560, 1440], 'temporal_samples': 8, 'spatial_samples': 1,
    'layers_ok': True, 'can_png': True, 'can_exr': False, 'can_sound': True,
    'game_overrides': 'implicit', 'texture_streaming': 'unchanged',
    'custom_frame_rate': None, 'frame_step': 1, 'handle_frames': 0,
    'frame_number_offset': 0, 'zero_pad': 4,
}

SHOTS = [['sh010_open', 0, 120], ['sh020_walk', 120, 300], ['sh030_door', 300, 360]]


def shots_body(**overrides):
    """A ``shots-result/`` body that passes every rule; keys replaced by ``overrides``."""
    body = {
        'preset_kind': 'legacy',
        'playback_range': [0, 360],
        'frame_rate': [30, 1],
        'shot_source': 'shot_tracks',
        'shots': copy.deepcopy(SHOTS),
        'preset_facts': dict(PRESET_FACTS),
        'warnings': [],
    }
    body.update(copy.deepcopy(overrides))
    return body


def with_facts(**facts):
    """A body whose ``preset_facts`` has some keys replaced."""
    return shots_body(preset_facts=dict(PRESET_FACTS, **facts))
