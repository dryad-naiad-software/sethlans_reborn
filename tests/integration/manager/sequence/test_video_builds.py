# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the sequence builds through the work queue (Unreal
sequence spec FR-SV-1..7, D13, D15, D16, AC-18, AC-19, AC-35), with a fake
ffmpeg and real sound cuts.

Covers: once the shots are DONE their items are PENDING and the version is
ASSEMBLING; the queue builds every shot video (``-framerate num/den``, the
fixed track timescale, the shot's first frame), sound cut and merged copy,
then the whole video by concat (quoted list, ``-safe 0`` with
``-protocol_whitelist file``), the whole sound and the whole merged copy,
stores them under index-named folders and ends DONE; a failing ffmpeg and
a raising task leave the items ERROR and the version final; a joined MP4
whose frame count misses fails, a WebM is encoded from the frames; a
PARTLY_DONE version builds its DONE shots and no whole video.
"""

import os
import wave
from unittest import mock

import pytest

from workers.models import SequenceShot

from ..unreal_helpers import unreal_asset
from .build_helpers import (
    RUN_FFMPEG, FakeFfmpeg, build_all, finish_pieces, finish_sound, items, manual_queue, version,
)
from .submission_helpers import VIDEO, shots_of, small_shots_body


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


def media(settings, name):
    return os.path.realpath(os.path.join(settings.MEDIA_ROOT, name))


@pytest.mark.django_db
class TestFullBuild:

    def test_videos_sound_and_merged_copies(self, admin_client, asset, worker_with_token, settings,
                                            django_capture_on_commit_callbacks):
        worker, client = worker_with_token
        submission = version(admin_client, asset, worker, client, video_settings=VIDEO, sound=True, merge_sound=True)
        finish_sound(client, worker, submission)
        finish_pieces(client, worker, submission)
        submission.refresh_from_db()
        assert submission.status == 'ASSEMBLING'
        assert {items(shot) for shot in shots_of(submission)} == {('PENDING', 'PENDING', None)}
        fake = FakeFfmpeg()
        with manual_queue(), mock.patch(RUN_FFMPEG, fake):
            build_all(django_capture_on_commit_callbacks, submission.pk)
        submission.refresh_from_db()
        assert (submission.status, items(submission)) == ('DONE', ('DONE', 'DONE', 'DONE'))
        base = f'sequences/{asset.project_id}/{submission.pk}'
        assert (submission.video_file.name, submission.sound_file.name, submission.sound_video_file.name) == (
            f'{base}/whole/video.mp4', f'{base}/whole/sound.wav', f'{base}/whole/video_sound.mp4')
        for shot in shots_of(submission):
            assert items(shot) == ('DONE', 'DONE', 'DONE')
            assert shot.video_file.name == f'{base}/shots/{shot.index:04d}/video.mp4'
            assert os.path.isfile(media(settings, shot.sound_video_file.name))
            with wave.open(media(settings, shot.sound_file.name), 'rb') as cut:
                assert cut.getnframes() == (shot.end_frame - shot.start_frame) * 1600
        with wave.open(media(settings, submission.sound_file.name), 'rb') as whole:
            assert whole.getnframes() == 14 * 1600
        frames = fake.of_kind('frames')
        assert len(frames) == 3
        for cmd, shot in zip(sorted(frames, key=lambda c: int(c[c.index('-start_number') + 1])), shots_of(submission)):
            assert cmd[cmd.index('-framerate') + 1] == '30/1'
            assert cmd[cmd.index('-start_number') + 1] == str(shot.start_frame)
            assert cmd[cmd.index('-frames:v') + 1] == str(shot.end_frame - shot.start_frame)
            assert cmd[cmd.index('-video_track_timescale') + 1] == '90000'
        concat = fake.of_kind('concat')[0]
        assert concat[concat.index('-safe') + 1] == '0'
        assert concat[concat.index('-protocol_whitelist') + 1] == 'file'
        assert concat[concat.index('-c') + 1] == 'copy'
        listed = [line for line in fake.lists[0].splitlines()]
        assert listed == ["file '" + media(settings, shot.video_file.name) + "'" for shot in shots_of(submission)]
        muxes = fake.of_kind('mux')
        assert len(muxes) == 4 and all(cmd[cmd.index('-c:a') + 1] == 'aac' for cmd in muxes)
        assert len(fake.of_kind('probe')) == 1

    @pytest.mark.parametrize('rate, timescale', [([30000, 1001], '90000'), ([24000, 1001], '96000'),
                                                 ([60000, 1001], '120000'), ([24, 1], '90000')], ids=str)
    def test_rational_rate(self, admin_client, asset, worker_with_token, django_capture_on_commit_callbacks,
                           rate, timescale):
        """Review: the timescale is a multiple of num, so every frame lasts the same whole number of ticks."""
        worker, client = worker_with_token
        body = small_shots_body(frame_rate=rate)
        submission = version(admin_client, asset, worker, client, body=body, video_settings=VIDEO, sound=False)
        finish_pieces(client, worker, submission)
        fake = FakeFfmpeg()
        with manual_queue(), mock.patch(RUN_FFMPEG, fake):
            build_all(django_capture_on_commit_callbacks, submission.pk)
        submission.refresh_from_db()
        assert (submission.status, submission.video_status) == ('DONE', 'DONE')
        for cmd in fake.of_kind('frames') + fake.of_kind('concat'):
            assert cmd[cmd.index('-video_track_timescale') + 1] == timescale
        assert {cmd[cmd.index('-framerate') + 1] for cmd in fake.of_kind('frames')} == {f'{rate[0]}/{rate[1]}'}


@pytest.mark.django_db
class TestFailures:

    def _built(self, admin_client, asset, worker_with_token, capture, fake, video=VIDEO, fail_last=False):
        worker, client = worker_with_token
        submission = version(admin_client, asset, worker, client, video_settings=video, sound=False)
        finish_pieces(client, worker, submission, fail_last=fail_last)
        with manual_queue(), mock.patch(RUN_FFMPEG, fake):
            build_all(capture, submission.pk)
        submission.refresh_from_db()
        return submission

    def test_failing_ffmpeg_leaves_errors_and_a_final_version(self, admin_client, asset, worker_with_token,
                                                              django_capture_on_commit_callbacks):
        submission = self._built(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks,
                                 FakeFfmpeg(code=1))
        assert submission.status == 'DONE' and submission.completed_at is not None
        assert {(s.video_status, s.video_error) for s in shots_of(submission)} == {
            ('ERROR', "FFmpeg failed (exit 1); see the manager log.")}
        assert (submission.video_status, submission.video_error) == ('ERROR', "A shot video failed; retry the videos.")
        assert SequenceShot.objects.filter(submission=submission, video_status='ASSEMBLING').count() == 0

    def test_a_raising_task_stores_only_the_class_name(self, admin_client, asset, worker_with_token,
                                                       django_capture_on_commit_callbacks):
        with mock.patch('workers.video_sequence.build', side_effect=RuntimeError('C:/secret/path')):
            submission = self._built(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks,
                                     FakeFfmpeg())
        assert submission.status == 'DONE'
        assert {s.video_error for s in shots_of(submission)} == {"The build failed (RuntimeError)."}

    def test_mp4_frame_count_mismatch_fails_the_whole_video(self, admin_client, asset, worker_with_token,
                                                            django_capture_on_commit_callbacks):
        submission = self._built(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks,
                                 FakeFfmpeg(frames=13))
        assert (submission.video_status, submission.video_error) == (
            'ERROR', "The joined video has 13 frames; its shots have 14.")
        assert {s.video_status for s in shots_of(submission)} == {'DONE'}
        assert submission.status == 'DONE'

    def test_uneven_mp4_frame_durations_fail_the_whole_video(self, admin_client, asset, worker_with_token,
                                                             django_capture_on_commit_callbacks):
        submission = self._built(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks,
                                 FakeFfmpeg(uneven=True))
        assert (submission.video_status, submission.video_error) == (
            'ERROR', "The joined video's frames do not all last the same time.")

    def test_webm_frame_count_mismatch_encodes_from_linked_frames(self, admin_client, asset, worker_with_token,
                                                                  settings, django_capture_on_commit_callbacks):
        fake = FakeFfmpeg(frames=[13, 14])
        submission = self._built(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks,
                                 fake, video={'preset': 'web_vp9'})
        assert submission.video_status == 'DONE'
        assert submission.video_file.name.endswith('/whole/video.webm')
        whole = fake.of_kind('frames')[-1]
        assert whole[whole.index('-frames:v') + 1] == '14' and whole[whole.index('-start_number') + 1] == '1'
        assert '-video_track_timescale' not in whole
        pattern, listed = fake.patterns[-1]
        folder = os.path.dirname(pattern)
        assert os.path.commonpath([folder, os.path.realpath(settings.MEDIA_ROOT)]) == os.path.realpath(
            settings.MEDIA_ROOT)
        assert listed == [f'{n:07d}.png' for n in range(1, 15)] and not os.path.exists(folder)
        assert len(fake.of_kind('probe')) == 2

    def test_webm_fallback_that_still_misses_fails(self, admin_client, asset, worker_with_token,
                                                   django_capture_on_commit_callbacks):
        submission = self._built(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks,
                                 FakeFfmpeg(frames=13), video={'preset': 'web_vp9'})
        assert (submission.video_status, submission.video_error) == (
            'ERROR', "The joined video has 13 frames; its shots have 14.")

    def test_a_build_past_its_time_budget_fails(self, admin_client, asset, worker_with_token,
                                                django_capture_on_commit_callbacks):
        with mock.patch('workers.video_sequence_build.BUILD_SECONDS', 0):
            submission = self._built(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks,
                                     FakeFfmpeg())
        assert {s.video_error for s in shots_of(submission)} == {"The build took longer than 1 hour."}

    def test_semaphore_is_held_only_during_each_run(self, admin_client, asset, worker_with_token,
                                                    django_capture_on_commit_callbacks):
        events, fake = [], FakeFfmpeg()

        class Recorder:
            def __enter__(self):
                events.append('acquire')

            def __exit__(self, *_exc):
                events.append('release')

        def run(*args, **kwargs):
            events.append('run')
            return fake(*args, **kwargs)

        with mock.patch('workers.video_sequence_build._assembly_semaphore', Recorder()):
            submission = self._built(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks, run)
        assert submission.video_status == 'DONE'
        assert events == ['acquire', 'run', 'release'] * len(fake.commands) and len(fake.commands) == 5

    def test_partly_done_builds_done_shots_and_no_whole_video(self, admin_client, asset, worker_with_token,
                                                              django_capture_on_commit_callbacks):
        fake = FakeFfmpeg()
        submission = self._built(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks,
                                 fake, fail_last=True)
        assert submission.status == 'PARTLY_DONE'
        assert [(s.status, s.video_status) for s in shots_of(submission)] == [
            ('DONE', 'DONE'), ('DONE', 'DONE'), ('ERROR', None)]
        assert submission.video_status is None and not fake.of_kind('concat')
