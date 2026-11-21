# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the text parts of sequence downloads and routing notes
(Unreal sequence spec FR-SL-3, FR-SD-6, FR-SR-4).

Covers: download file names from slugs, a shot slug falling back to
``shot_<nnn>``; the routing note's output-size text for a piece with a
chosen size, a piece with the preset's size, a sequence pilot, a
standalone job with a size, a job without a known size and an audio job.
"""

from types import SimpleNamespace

import pytest

from workers.renderers.unreal_sequence import output_size_text
from workers.views.sequence_downloads import download_name, shot_slug


def _submission(project='Harbor Show', sequence='/Game/Cinematics/Ep 01.Ep 01', version=3):
    return SimpleNamespace(project=SimpleNamespace(name=project), sequence=sequence, version=version)


class TestNames:

    def test_whole_and_shot(self):
        submission = _submission()
        assert download_name(submission, 'mp4') == 'harbor-show_ep-01_v3.mp4'
        shot = SimpleNamespace(name='Sh010.Final', index=1)
        assert download_name(submission, 'zip', shot) == 'harbor-show_ep-01_v3_sh010final.zip'

    @pytest.mark.parametrize('name, expected', [('___', 'shot_007'), ('', 'shot_007'), ('a-b_c', 'a-b_c')],
                             ids=['underscores', 'empty', 'kept'])
    def test_shot_slug(self, name, expected):
        assert shot_slug(name, 7) == expected

    def test_empty_project_and_sequence(self):
        assert download_name(_submission(project='***', sequence='/Game/.x'), 'wav') == 'project_sequence_v3.wav'


def _job(kind='render', settings=None, submission=None):
    return SimpleNamespace(unreal_kind=kind, render_settings=settings or {}, sequence_submission_id=submission)


class TestSizeText:

    @pytest.mark.parametrize('job, expected', [
        (_job(settings={'unreal.resolution': [1920, 1080], 'unreal.output_size': [1920, 1080]}, submission=4),
         "at 1920x1080 (the submission's size)"),
        (_job(settings={'unreal.output_size': [2560, 1440]}, submission=4), "at 2560x1440 (the preset's size)"),
        (_job(kind='pilot', settings={'unreal.resolution': [1280, 720], 'unreal.pilot_frames': [0]}),
         "at 1280x720 (the submission's size)"),
        (_job(settings={'unreal.resolution': [1280, 720]}), "at 1280x720 (the job's size)"),
        (_job(), "at the preset's size"),
        (_job(settings={'unreal.resolution': [True, 5]}), "at the preset's size"),
        (_job(kind='audio', submission=4), "at 128x72 (the sound render's size)"),
    ], ids=['chosen', 'preset', 'sequence-pilot', 'standalone', 'unknown', 'bad-value', 'audio'])
    def test_text(self, job, expected):
        assert output_size_text(job) == expected
