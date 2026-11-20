# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the sequence measurement key and the client-facing job
rules of chunk 3 (Unreal sequence spec D8, FR-SR-1, FR-SW-2, D12, D18,
AC-9, AC-41).

Covers: ``target_sha256`` keeps its four-part form without a resolution or
shot set and changes with either; ``shot_set_sha256`` ignores order; an
unsaved job's ``compute_target_sha256`` equals ``Target.of(job).sha256()``;
``Target.pilot_settings`` and ``pilot_frame``; the manager-only
``render_settings`` keys and EXR refused from clients, ``unreal.resolution``
checked; ``validate_sound_offsets``; the piece rules of ``job_kind_rules``.
No database.  Object paths are made up.
"""

from types import SimpleNamespace

import pytest

from shared.unreal.validation import _frozen_sha256, shot_set_sha256, target_sha256
from workers.constants import UnrealKind
from workers.constants_sequence import MANAGER_ONLY_KEYS, PILOT_FRAMES_KEY, RESOLUTION_KEY, SHOT_SET_KEY
from workers.models import Job
from workers.renderers.unreal_settings import EXR_MESSAGE, render_settings_errors
from workers.renderers.unreal_validation import validate_sound_offsets
from workers.services.job_kind_rules import done_only_by_result, is_piece, never_requeued, pilot_done_message
from workers.services.unreal_target import Target

BUILD = '5.8.4-58902052'
MAP, SEQ, PRESET = '/Game/Maps/Dock.Dock', '/Game/Cine/Ep02.Ep02', '/Game/Presets/Farm.Farm'
BASE = {'unreal.map': MAP, 'unreal.sequence': SEQ, 'unreal.preset': PRESET}
SHOT_SET = shot_set_sha256([(0, 120), (300, 360)])


class TestTargetHash:

    def test_old_form_unchanged(self):
        assert target_sha256(BUILD, MAP, SEQ, PRESET) == _frozen_sha256([BUILD, MAP, SEQ, PRESET])
        assert target_sha256(BUILD, MAP, SEQ, PRESET, None, None) == _frozen_sha256([BUILD, MAP, SEQ, PRESET])
        assert target_sha256(BUILD, MAP, SEQ, PRESET, None, '') == _frozen_sha256([BUILD, MAP, SEQ, PRESET])

    def test_resolution_and_shot_set_change_it(self):
        plain = target_sha256(BUILD, MAP, SEQ, PRESET)
        sized = target_sha256(BUILD, MAP, SEQ, PRESET, [1920, 1080])
        shots = target_sha256(BUILD, MAP, SEQ, PRESET, None, SHOT_SET)
        both = target_sha256(BUILD, MAP, SEQ, PRESET, [1920, 1080], SHOT_SET)
        assert len({plain, sized, shots, both}) == 4
        assert sized == _frozen_sha256([BUILD, MAP, SEQ, PRESET, '1920x1080', ''])
        assert target_sha256(BUILD, MAP, SEQ, PRESET, (1920, 1080)) == sized
        assert target_sha256(BUILD, MAP, SEQ, PRESET, [1280, 720]) != sized

    def test_shot_set_order_free(self):
        assert shot_set_sha256([(300, 360), (0, 120)]) == SHOT_SET
        assert shot_set_sha256([[0, 120], [300, 361]]) != SHOT_SET
        assert shot_set_sha256([]) != SHOT_SET


def _job(**settings):
    return Job(renderer='UNREAL', engine_build=BUILD, render_settings=dict(BASE, **settings))


class TestJobAndTarget:
    """D8: the job row and ``Target.of`` give the same key."""

    @pytest.mark.parametrize('settings', [
        {}, {RESOLUTION_KEY: [1920, 1080]}, {SHOT_SET_KEY: SHOT_SET},
        {RESOLUTION_KEY: [1920, 1080], SHOT_SET_KEY: SHOT_SET, 'unreal.output_format': 'PNG'},
    ])
    def test_same_hash(self, settings):
        job = _job(**settings)
        assert job.compute_target_sha256() == Target.of(job).sha256()

    def test_standalone_keeps_its_key(self):
        assert _job().compute_target_sha256() == target_sha256(BUILD, MAP, SEQ, PRESET)

    def test_blender_job_has_no_key(self):
        assert Job(renderer='BLENDER', render_settings=dict(BASE)).compute_target_sha256() == ''

    def test_pilot_settings(self):
        target = Target(BUILD, MAP, SEQ, PRESET, (1920, 1080), SHOT_SET, (0, 300))
        assert target.pilot_settings() == {
            RESOLUTION_KEY: [1920, 1080], SHOT_SET_KEY: SHOT_SET, PILOT_FRAMES_KEY: [0, 300],
        }
        assert target.pilot_frame(77) == 0
        assert Target(BUILD, MAP, SEQ, PRESET).pilot_settings() == {}
        assert Target(BUILD, MAP, SEQ, PRESET).pilot_frame(77) == 77

    def test_pilot_frames_not_in_the_key(self):
        a = Target(BUILD, MAP, SEQ, PRESET, None, SHOT_SET, (0,))
        b = Target(BUILD, MAP, SEQ, PRESET, None, SHOT_SET, (0, 300))
        assert a == b and a.sha256() == b.sha256()


class TestClientSettings:
    """FR-SW-2: manager-only keys and EXR are refused from clients."""

    @pytest.mark.parametrize('key', MANAGER_ONLY_KEYS)
    def test_manager_only_key(self, key):
        problems = render_settings_errors(dict(BASE, **{key: 'x'}))
        assert problems == [f"'{key}' is set by sequence renders only."]

    def test_exr(self):
        assert render_settings_errors(dict(BASE, **{'unreal.output_format': 'EXR'})) == [EXR_MESSAGE]

    @pytest.mark.parametrize('value', [[64, 64], [16384, 16384], [1920, 1080], (640, 360)])
    def test_resolution_accepted(self, value):
        assert render_settings_errors(dict(BASE, **{RESOLUTION_KEY: value})) == []

    @pytest.mark.parametrize('value', [[True, 1080], [63, 1080], [1920, 16385], [1920, 1080, 1], '1920x1080', [1920.0, 1080]])
    def test_resolution_refused(self, value):
        problems = render_settings_errors(dict(BASE, **{RESOLUTION_KEY: value}))
        assert len(problems) == 1 and RESOLUTION_KEY in problems[0]

    def test_unknown_key_still_unknown(self):
        assert render_settings_errors(dict(BASE, **{'unreal.other': 1})) == ["Unknown Unreal render settings: unreal.other."]


class TestSoundOffsets:
    """D18, AC-41."""

    @pytest.mark.parametrize('value', [{}, {f'{PRESET}@30/1': 196}, {f'{PRESET}@30000/1001': -10_000},
                                       {f'{PRESET}@24/1': 10_000}])
    def test_accepted(self, value):
        assert validate_sound_offsets(value) == value

    @pytest.mark.parametrize('value', [
        {f'{PRESET}@30/1': 10_001}, {f'{PRESET}@30/1': -10_001}, {f'{PRESET}@30/1': True},
        {f'{PRESET}@30/1': 1.5}, {f'{PRESET}@30/1': '196'},
        {f'{PRESET}30/1': 0}, {'@30/1': 0}, {f'{PRESET}@30': 0}, {f'{PRESET}@0/1': 0},
        {f'{PRESET}@30/1002': 0}, {f'{PRESET}@x/1': 0}, {'not a path@30/1': 0},
        [], 'x',
    ])
    def test_refused(self, value):
        with pytest.raises(ValueError):
            validate_sound_offsets(value)

    def test_at_most_64_entries(self):
        many = {f'/Game/Presets/P{i}.P{i}@30/1': 0 for i in range(65)}
        with pytest.raises(ValueError, match='64'):
            validate_sound_offsets(many)
        many.popitem()
        assert len(validate_sound_offsets(many)) == 64


class TestPieceRules:
    """D12: a piece becomes DONE only through ``piece-result/`` and may be requeued."""

    def _job(self, kind=UnrealKind.RENDER, submission=9):
        return SimpleNamespace(unreal_kind=kind, sequence_submission_id=submission, pk=42,
                               asset=SimpleNamespace(project_id=7))

    def test_piece(self):
        job = self._job()
        assert is_piece(job) and done_only_by_result(job) and not never_requeued(job)
        assert pilot_done_message(job) == "Piece jobs complete through POST /api/jobs/42/piece-result/."

    def test_standalone_render(self):
        job = self._job(submission=None)
        assert not is_piece(job) and not done_only_by_result(job)

    def test_audio_job_of_a_submission_is_not_a_piece(self):
        job = self._job(kind=UnrealKind.AUDIO)
        assert not is_piece(job)
        assert pilot_done_message(job) == "Audio jobs complete through POST /api/jobs/42/sound-result/."
