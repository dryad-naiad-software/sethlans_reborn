# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the submission rules ``serializers/sequence_validation.py``
(Unreal sequence spec FR-SQ-3, FR-SQ-5..8, D5b, AC-7, AC-8, AC-38).

Covers: ``request_errors`` (one selection, frame range pair and order,
distinct shots, resolution pair, EXR with video, merge without sound or
video, draft keys and values); ``facts_errors`` against a stub discovery
(unknown shots, range outside the sequence, the preset's custom frame
rate, EXR and sound support, draft samples above the preset's, even video
size for an override and for the preset's size, an unread preset size);
``facts_warnings``; ``effective_size``; ``submission_values``.  No
database.  Shot names are made up.
"""

from types import SimpleNamespace

import pytest

from workers.serializers.sequence_validation import (
    MERGE_NEEDS, NO_EXR, NO_SIZE, NO_SOUND, ONE_SELECTION, RANGE_ORDER, RANGE_PAIR, RESOLUTION_PAIR,
    STEP_OR_HANDLES, draft_error, effective_size, facts_errors, facts_warnings, request_errors, submission_values,
)

VIDEO = {'preset': 'web_h264', 'container': 'mp4', 'codec': 'libx264', 'crf': 23}
FACTS = {
    'resolution': [2560, 1440], 'temporal_samples': 8, 'spatial_samples': 2, 'can_exr': True, 'can_sound': True,
    'custom_frame_rate': None, 'frame_step': 1, 'handle_frames': 0,
}


def _discovery(rate=(30, 1), **facts):
    return SimpleNamespace(
        shots=[{'index': 1, 'name': 'sh010', 'start': 0, 'end': 50},
               {'index': 2, 'name': 'sh020', 'start': 50, 'end': 120}],
        playback_start=0, playback_end=120, frame_rate_num=rate[0], frame_rate_den=rate[1],
        preset_facts=dict(FACTS, **facts),
    )


class TestRequestErrors:

    def test_plain_body_is_fine(self):
        assert request_errors({'frame_format': 'PNG', 'sound': True}) == {}

    def test_shots_and_range(self):
        errors = request_errors({'shots': ['sh010'], 'frame_start': 0, 'frame_end': 9})
        assert errors['shots'] == ONE_SELECTION

    @pytest.mark.parametrize('data', [{'frame_start': 3}, {'frame_end': 3}])
    def test_range_pair(self, data):
        assert request_errors(data) == {'frame_start': RANGE_PAIR}

    def test_range_order(self):
        assert request_errors({'frame_start': 10, 'frame_end': 9}) == {'frame_start': RANGE_ORDER}

    def test_single_frame_range(self):
        assert request_errors({'frame_start': 9, 'frame_end': 9}) == {}

    def test_distinct_shots(self):
        assert 'distinct' in request_errors({'shots': ['sh010', 'sh010']})['shots']

    @pytest.mark.parametrize('data', [{'resolution_x': 640}, {'resolution_y': 360}])
    def test_resolution_pair(self, data):
        assert request_errors(data) == {'resolution_x': RESOLUTION_PAIR}

    def test_exr_with_video(self):
        errors = request_errors({'frame_format': 'EXR', 'video_settings': VIDEO})
        assert errors['video_settings'].startswith("Video output is not available for EXR format. Use PNG")

    @pytest.mark.parametrize('data', [
        {'merge_sound': True, 'sound': False, 'video_settings': VIDEO},
        {'merge_sound': True, 'sound': True, 'video_settings': None},
        {'merge_sound': True, 'sound': True},
    ])
    def test_merge_needs_sound_and_video(self, data):
        assert request_errors(data) == {'merge_sound': MERGE_NEEDS}

    def test_merge_with_both(self):
        assert request_errors({'merge_sound': True, 'sound': True, 'video_settings': VIDEO}) == {}


class TestDraft:
    """FR-SQ-7, AC-7: only the draft keys, only lowering values."""

    @pytest.mark.parametrize('draft', [
        None, {}, {'temporal_samples': 1, 'spatial_samples': 3, 'screen_percentage': 10},
        {'screen_percentage': 100, 'motion_blur': False, 'ray_traced_shadows': False, 'ray_traced_reflections': False},
    ])
    def test_accepted(self, draft):
        assert draft_error(draft) is None

    @pytest.mark.parametrize('draft, named', [
        ({'denoise': False}, 'denoise'),
        ({'screen_percentage': 150}, 'screen_percentage'),
        ({'screen_percentage': 9}, 'screen_percentage'),
        ({'motion_blur': True}, 'motion_blur'),
        ({'ray_traced_shadows': 0}, 'ray_traced_shadows'),
        ({'temporal_samples': 0}, 'temporal_samples'),
        ({'spatial_samples': True}, 'spatial_samples'),
        ({'temporal_samples': 2.0}, 'temporal_samples'),
    ])
    def test_refused_naming_the_key(self, draft, named):
        assert named in draft_error(draft)
        assert named in request_errors({'draft': draft})['draft']

    def test_not_an_object(self):
        assert draft_error([1]) == "draft must be an object or null."


class TestFactsErrors:

    def test_nothing_wrong(self):
        data = {'shots': ['sh020'], 'frame_format': 'EXR', 'sound': True, 'draft': {'temporal_samples': 8}}
        assert facts_errors(data, _discovery()) == {}

    def test_unknown_shots(self):
        assert facts_errors({'shots': ['sh010', 'sh999']}, _discovery()) == {'shots': "Unknown shots: sh999."}

    @pytest.mark.parametrize('start, end', [(-1, 10), (100, 120), (130, 140)])
    def test_range_outside(self, start, end):
        errors = facts_errors({'frame_start': start, 'frame_end': end}, _discovery())
        assert errors == {'frame_start': "The frame range is outside the sequence (0-119)."}

    def test_range_inside(self):
        assert facts_errors({'frame_start': 0, 'frame_end': 119}, _discovery()) == {}

    def test_custom_rate_differs(self):
        errors = facts_errors({}, _discovery(rate=(30, 1), custom_frame_rate=[24, 1]))
        assert errors['preset'].startswith("The preset renders at 24/1 fps but the sequence runs at 30/1 fps")

    @pytest.mark.parametrize('custom', [[30, 1], [60, 2]])
    def test_equal_custom_rate_accepted(self, custom):
        assert facts_errors({}, _discovery(rate=(30, 1), custom_frame_rate=custom)) == {}

    def test_exr_without_output_node(self):
        assert facts_errors({'frame_format': 'EXR'}, _discovery(can_exr=False)) == {'frame_format': NO_EXR}

    def test_sound_without_audio_output(self):
        assert facts_errors({'sound': True}, _discovery(can_sound=False)) == {'sound': NO_SOUND}
        assert facts_errors({'sound': False}, _discovery(can_sound=False)) == {}

    @pytest.mark.parametrize('key, value', [('temporal_samples', 9), ('spatial_samples', 3)])
    def test_draft_samples_above_the_preset(self, key, value):
        errors = facts_errors({'draft': {key: value}}, _discovery())
        assert key in errors['draft']

    def test_draft_samples_when_preset_sets_none(self):
        assert facts_errors({'draft': {'spatial_samples': 2}}, _discovery(spatial_samples=None))['draft']

    def test_odd_override_with_video(self):
        data = {'video_settings': VIDEO, 'resolution_x': 641, 'resolution_y': 360}
        errors = facts_errors(data, _discovery())
        assert errors == {'resolution_x': "Video needs an even width and height; the output is 641x360."}

    def test_odd_preset_size_with_video(self):
        errors = facts_errors({'video_settings': VIDEO}, _discovery(resolution=[1920, 1081]))
        assert errors['resolution_x'].endswith("the output is 1920x1081.")

    def test_odd_preset_size_fixed_by_an_override(self):
        data = {'video_settings': VIDEO, 'resolution_x': 1920, 'resolution_y': 1080}
        assert facts_errors(data, _discovery(resolution=[1920, 1081])) == {}

    def test_odd_size_without_video(self):
        assert facts_errors({}, _discovery(resolution=[1920, 1081])) == {}

    def test_unread_preset_size_with_video(self):
        assert facts_errors({'video_settings': VIDEO}, _discovery(resolution=None)) == {'resolution_x': NO_SIZE}


class TestHelpers:

    @pytest.mark.parametrize('facts, expected', [
        ({}, []), ({'frame_step': 2}, [STEP_OR_HANDLES]), ({'handle_frames': 4}, [STEP_OR_HANDLES]),
        ({'frame_step': None, 'handle_frames': None}, []),
    ])
    def test_facts_warnings(self, facts, expected):
        assert facts_warnings(_discovery(**facts)) == expected

    def test_effective_size(self):
        assert effective_size({'resolution_x': 640, 'resolution_y': 360}, FACTS) == [640, 360]
        assert effective_size({}, FACTS) == [2560, 1440]
        assert effective_size({}, {'resolution': None}) is None

    def test_submission_values(self):
        stored = SimpleNamespace(
            frame_format='PNG', video_settings=None, sound=True, draft=None, selected_shots=None,
            frame_start=10, frame_end=20, resolution_x=None, resolution_y=None,
        )
        assert submission_values(stored) == {
            'frame_format': 'PNG', 'video_settings': None, 'sound': True, 'draft': None, 'shots': None,
            'frame_start': 10, 'frame_end': 20,
        }
        stored.frame_start = stored.frame_end = None
        stored.resolution_x, stored.resolution_y = 640, 360
        values = submission_values(stored)
        assert 'frame_start' not in values and (values['resolution_x'], values['resolution_y']) == (640, 360)
