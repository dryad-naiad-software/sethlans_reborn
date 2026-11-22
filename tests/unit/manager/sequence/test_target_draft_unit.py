# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the draft and coverage fields of ``services.unreal_target.Target``
and ``Job.compute_target_sha256`` (Unreal per-machine memory test OQ-1, D8,
FR-MT-R-3).

Covers: ``draft`` is part of the key and order-free; ``measured_ranges`` is
not; ``base_sha256()`` drops shot set and draft; ``rendered_ranges()`` keeps
only pairs whose start frame the pilot renders; ``pilot_settings`` carries
``unreal.draft``; ``Target.of`` reads the draft from the job and the ranges
from the submission (query mocked); an unsaved job's key with a draft equals
``shared`` ``target_sha256`` and ``Target.of(job).sha256()``, also when the
draft is not an object (then both equal the key without a draft).  No
database.
"""

import pytest

from shared.unreal.validation import shot_set_sha256, target_sha256
from workers.constants_sequence import DRAFT_KEY, PILOT_FRAMES_KEY, RESOLUTION_KEY, SHOT_SET_KEY
from workers.models import Job
from workers.services.unreal_target import Target

BUILD = '5.8.4-58902052'
MAP, SEQ, PRESET = '/Game/Maps/Dock.Dock', '/Game/Cine/Ep02.Ep02', '/Game/Presets/Farm.Farm'
BASE = {'unreal.map': MAP, 'unreal.sequence': SEQ, 'unreal.preset': PRESET}
SHOT_SET = shot_set_sha256([(0, 120), (300, 360)])
OTHER_SET = shot_set_sha256([(0, 120)])
DRAFT = {'temporal_samples': 1, 'screen_percentage': 50, 'motion_blur': False}
DRAFT_PAIRS = (('motion_blur', False), ('screen_percentage', 50), ('temporal_samples', 1))
SIZE = (1920, 1080)


def _target(**fields):
    return Target(BUILD, MAP, SEQ, PRESET, **fields)


def _job(**settings):
    return Job(renderer='UNREAL', engine_build=BUILD, render_settings=dict(BASE, **settings))


class TestDraftField:

    def test_default_is_empty(self):
        assert _target().draft == () and _target().draft_settings() is None

    def test_draft_settings_gives_the_object(self):
        assert _target(draft=DRAFT_PAIRS).draft_settings() == DRAFT

    def test_draft_is_part_of_the_key(self):
        plain, drafted = _target(resolution=SIZE), _target(resolution=SIZE, draft=DRAFT_PAIRS)
        assert plain != drafted
        assert plain.sha256() != drafted.sha256()

    def test_sha256_matches_shared_hash(self):
        target = _target(resolution=SIZE, shot_set_sha256=SHOT_SET, draft=DRAFT_PAIRS)
        assert target.sha256() == target_sha256(BUILD, MAP, SEQ, PRESET, SIZE, SHOT_SET, DRAFT)

    def test_no_draft_keeps_the_old_key(self):
        target = _target(resolution=SIZE, shot_set_sha256=SHOT_SET)
        assert target.sha256() == target_sha256(BUILD, MAP, SEQ, PRESET, SIZE, SHOT_SET)

    def test_target_with_draft_is_hashable(self):
        assert hash(_target(draft=DRAFT_PAIRS)) == hash(_target(draft=DRAFT_PAIRS))


class TestMeasuredRanges:

    def test_not_part_of_the_key(self):
        a = _target(shot_set_sha256=SHOT_SET, measured_ranges=((0, 120),))
        b = _target(shot_set_sha256=SHOT_SET, measured_ranges=((0, 120), (300, 360)))
        assert a == b and a.sha256() == b.sha256() and a.base_sha256() == b.base_sha256()

    def test_default_is_empty(self):
        assert _target().measured_ranges == ()


class TestBaseSha256:

    def test_equal_across_shot_sets_and_drafts(self):
        targets = [_target(resolution=SIZE), _target(resolution=SIZE, shot_set_sha256=SHOT_SET),
                   _target(resolution=SIZE, shot_set_sha256=OTHER_SET, draft=DRAFT_PAIRS),
                   _target(resolution=SIZE, draft=DRAFT_PAIRS)]
        assert len({t.sha256() for t in targets}) == 4
        assert len({t.base_sha256() for t in targets}) == 1

    def test_is_the_key_without_shot_set_and_draft(self):
        target = _target(resolution=SIZE, shot_set_sha256=SHOT_SET, draft=DRAFT_PAIRS)
        assert target.base_sha256() == target_sha256(BUILD, MAP, SEQ, PRESET, SIZE)
        assert target.base_sha256() == _target(resolution=SIZE).sha256()

    def test_without_size_is_the_four_part_key(self):
        assert _target(shot_set_sha256=SHOT_SET, draft=DRAFT_PAIRS).base_sha256() == target_sha256(BUILD, MAP, SEQ, PRESET)

    @pytest.mark.parametrize('changed', [
        Target('5.8.4-1', MAP, SEQ, PRESET, SIZE), Target(BUILD, MAP + 'x', SEQ, PRESET, SIZE),
        Target(BUILD, MAP, SEQ + 'x', PRESET, SIZE), Target(BUILD, MAP, SEQ, PRESET + 'x', SIZE),
        Target(BUILD, MAP, SEQ, PRESET, (1280, 720)), Target(BUILD, MAP, SEQ, PRESET),
    ], ids=['build', 'map', 'sequence', 'preset', 'size', 'no-size'])
    def test_differs_when_the_base_differs(self, changed):
        assert changed.base_sha256() != _target(resolution=SIZE, draft=DRAFT_PAIRS).base_sha256()


class TestRenderedRanges:

    def test_none_without_ranges(self):
        assert _target(pilot_frames=(0, 300)).rendered_ranges() is None

    def test_keeps_pairs_whose_start_was_rendered(self):
        target = _target(pilot_frames=(0, 300), measured_ranges=((0, 120), (120, 300), (300, 360)))
        assert target.rendered_ranges() == [[0, 120], [300, 360]]

    def test_end_frame_alone_does_not_count(self):
        assert _target(pilot_frames=(120,), measured_ranges=((0, 120),)).rendered_ranges() == []

    def test_no_pilot_frames_keeps_nothing(self):
        assert _target(measured_ranges=((0, 120),)).rendered_ranges() == []

    def test_all_rendered_keeps_order(self):
        target = _target(pilot_frames=(300, 0), measured_ranges=((300, 360), (0, 120)))
        assert target.rendered_ranges() == [[300, 360], [0, 120]]


class TestPilotSettings:

    def test_carries_the_draft(self):
        target = _target(resolution=SIZE, shot_set_sha256=SHOT_SET, pilot_frames=(0,), draft=DRAFT_PAIRS,
                         measured_ranges=((0, 120),))
        assert target.pilot_settings() == {
            RESOLUTION_KEY: [1920, 1080], SHOT_SET_KEY: SHOT_SET, PILOT_FRAMES_KEY: [0], DRAFT_KEY: DRAFT,
        }

    def test_no_draft_no_key(self):
        assert DRAFT_KEY not in _target(resolution=SIZE).pilot_settings()

    def test_pilot_job_key_equals_the_target_key(self):
        target = _target(resolution=SIZE, shot_set_sha256=SHOT_SET, pilot_frames=(0,), draft=DRAFT_PAIRS)
        assert _job(**target.pilot_settings()).compute_target_sha256() == target.sha256()


class TestTargetOf:

    def test_draft_read_sorted(self):
        assert Target.of(_job(**{DRAFT_KEY: DRAFT})).draft == DRAFT_PAIRS

    def test_draft_key_order_free(self):
        reordered = dict(reversed(list(DRAFT.items())))
        assert Target.of(_job(**{DRAFT_KEY: reordered})) == Target.of(_job(**{DRAFT_KEY: DRAFT}))

    @pytest.mark.parametrize('value', [None, {}, [], 'x', 3], ids=['none', 'empty', 'list', 'str', 'int'])
    def test_no_or_malformed_draft_is_empty(self, value):
        assert Target.of(_job(**{DRAFT_KEY: value})).draft == ()

    def test_no_submission_no_ranges(self):
        assert Target.of(_job()).measured_ranges == () and Target.of(_job()).pilot_frames == ()

    def test_ranges_from_the_submission(self, mocker):
        model = mocker.patch('workers.models.SequenceSubmission')
        query = model.objects.filter.return_value.values_list.return_value
        query.first.return_value = ([0, 300], [[0, 120], [300, 360]])
        job = _job(**{SHOT_SET_KEY: SHOT_SET})
        job.sequence_submission_id = 9
        target = Target.of(job)
        assert target.pilot_frames == (0, 300)
        assert target.measured_ranges == ((0, 120), (300, 360))
        model.objects.filter.assert_called_once_with(pk=9)
        model.objects.filter.return_value.values_list.assert_called_once_with('pilot_frames', 'measured_ranges')

    @pytest.mark.parametrize('row', [None, (None, None), ([0], None)], ids=['no-row', 'nulls', 'no-ranges'])
    def test_missing_submission_values(self, mocker, row):
        model = mocker.patch('workers.models.SequenceSubmission')
        model.objects.filter.return_value.values_list.return_value.first.return_value = row
        job = _job()
        job.sequence_submission_id = 9
        assert Target.of(job).measured_ranges == ()


class TestJobKey:

    def test_draft_matches_shared_hash(self):
        job = _job(**{RESOLUTION_KEY: [1920, 1080], SHOT_SET_KEY: SHOT_SET, DRAFT_KEY: DRAFT})
        assert job.compute_target_sha256() == target_sha256(BUILD, MAP, SEQ, PRESET, [1920, 1080], SHOT_SET, DRAFT)

    @pytest.mark.parametrize('settings', [
        {DRAFT_KEY: DRAFT}, {RESOLUTION_KEY: [1920, 1080], DRAFT_KEY: DRAFT},
        {RESOLUTION_KEY: [1920, 1080], SHOT_SET_KEY: SHOT_SET, DRAFT_KEY: DRAFT}, {DRAFT_KEY: {}},
    ], ids=['draft', 'size-draft', 'all', 'empty-draft'])
    def test_job_and_target_agree(self, settings):
        job = _job(**settings)
        assert job.compute_target_sha256() == Target.of(job).sha256()

    @pytest.mark.parametrize('value', [[], ['temporal_samples', 1], 'x', 3, True, None],
                             ids=['empty-list', 'list', 'str', 'int', 'bool', 'none'])
    def test_job_and_target_agree_on_a_non_dict_draft(self, value):
        """A malformed draft is no draft in both, so the job and its pilot share one key."""
        job = _job(**{RESOLUTION_KEY: [1920, 1080], DRAFT_KEY: value})
        plain = _job(**{RESOLUTION_KEY: [1920, 1080]})
        assert job.compute_target_sha256() == Target.of(job).sha256() == plain.compute_target_sha256()

    def test_draft_changes_the_job_key(self):
        assert _job(**{DRAFT_KEY: DRAFT}).compute_target_sha256() != _job().compute_target_sha256()

    def test_empty_draft_keeps_the_job_key(self):
        assert _job(**{DRAFT_KEY: {}}).compute_target_sha256() == target_sha256(BUILD, MAP, SEQ, PRESET)

    def test_draft_key_order_free(self):
        reordered = dict(reversed(list(DRAFT.items())))
        assert _job(**{DRAFT_KEY: reordered}).compute_target_sha256() == _job(**{DRAFT_KEY: DRAFT}).compute_target_sha256()

    def test_blender_job_ignores_draft(self):
        assert Job(renderer='BLENDER', render_settings=dict(BASE, **{DRAFT_KEY: DRAFT})).compute_target_sha256() == ''
