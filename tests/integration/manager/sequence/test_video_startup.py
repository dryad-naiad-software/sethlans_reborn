# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the build queue's server start hook (Unreal
sequence spec D16, FR-SD-9, AC-24).

Covers: under a new boot id an item ASSEMBLING under an old boot id is
PENDING again, queued and built; one ASSEMBLING under the current boot id
is left alone; the hook settles stale discoveries, starts the queue once
and returns the same boot id when called again; a management command and
the app registry never reset anything or start the queue.
"""

from unittest import mock

import pytest
from django.core.management import call_command

from workers import video_queue, video_startup
from workers.models import SequenceShot, SequenceSubmission

from ..unreal_helpers import unreal_asset
from .build_helpers import (
    BOOT, RUN_FFMPEG, FakeFfmpeg, finish_pieces, manual_queue, version,
)
from .submission_helpers import VIDEO, ffmpeg_ready


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


pytestmark = pytest.mark.django_db


def _interrupted(admin_client, asset, worker_with_token, boot='old-boot'):
    """A version whose first shot video was being built by a process under ``boot``."""
    worker, client = worker_with_token
    submission = version(admin_client, asset, worker, client, video_settings=VIDEO, sound=False)
    finish_pieces(client, worker, submission)
    SequenceShot.objects.filter(submission=submission, index=1).update(video_status='ASSEMBLING', assembly_boot=boot)
    return submission


def _shot_video(submission, index=1):
    return SequenceShot.objects.filter(submission=submission, index=index).values_list(
        'video_status', flat=True).get()


class TestStartHook:

    @pytest.fixture(autouse=True)
    def _stopped_queue(self):
        video_queue.stop_for_tests()
        yield
        video_queue.stop_for_tests()

    def test_old_boot_is_reset_and_built(self, admin_client, asset, worker_with_token,
                                         django_capture_on_commit_callbacks):
        submission = _interrupted(admin_client, asset, worker_with_token)
        with mock.patch.object(video_queue, 'start', side_effect=video_queue.start_manual_for_tests) as start, \
                mock.patch('workers.services.sequence_discovery.settle_open_discoveries', return_value=0) as settle, \
                ffmpeg_ready(), mock.patch(RUN_FFMPEG, FakeFfmpeg()):
            with django_capture_on_commit_callbacks(execute=True):
                boot = video_startup.on_server_start()
            assert _shot_video(submission) == 'PENDING'
            with django_capture_on_commit_callbacks(execute=True):
                assert video_queue.run_pending_for_tests() >= 3
            assert video_startup.on_server_start() == boot
        start.assert_called_once_with(boot)
        settle.assert_called_once_with()
        assert boot != 'old-boot' and len(boot) == 36
        shot = SequenceShot.objects.get(submission=submission, index=1)
        assert (shot.video_status, shot.assembly_boot) == ('DONE', boot)

    def test_current_boot_is_left_alone(self, admin_client, asset, worker_with_token):
        submission = _interrupted(admin_client, asset, worker_with_token, boot=BOOT)
        assert video_startup.reset_stale(BOOT) == 0
        assert _shot_video(submission) == 'ASSEMBLING'
        assert video_startup.reset_stale('another-boot') == 1
        assert _shot_video(submission) == 'PENDING'

    def test_pending_versions_are_found(self, admin_client, asset, worker_with_token):
        submission = _interrupted(admin_client, asset, worker_with_token)
        assert video_startup.pending_submissions() == [submission.pk]
        SequenceShot.objects.filter(submission=submission).update(video_status='DONE')
        # Still ASSEMBLING with every item final (a crash before the refresh): found, and settled by the start.
        assert video_startup.pending_submissions() == [submission.pk]
        SequenceSubmission.objects.filter(pk=submission.pk).update(video_status='ERROR')
        assert video_startup.queue_pending() == 1
        submission.refresh_from_db()
        assert submission.status == 'DONE'
        assert video_startup.pending_submissions() == []

    def test_a_failing_step_does_not_stop_the_start(self, admin_client, asset, worker_with_token):
        with mock.patch.object(video_startup, 'reset_stale', side_effect=RuntimeError('db down')), \
                mock.patch.object(video_queue, 'start', side_effect=video_queue.start_manual_for_tests), \
                mock.patch('workers.services.sequence_discovery.settle_open_discoveries', return_value=0):
            boot = video_startup.on_server_start()
        assert video_queue.boot_id() == boot


class TestNotServing:

    def test_management_command_and_app_registry_reset_nothing(self, admin_client, asset, worker_with_token):
        video_queue.stop_for_tests()
        submission = _interrupted(admin_client, asset, worker_with_token)
        call_command('check', verbosity=0)
        assert _shot_video(submission) == 'ASSEMBLING'
        assert video_queue.is_accepting() is False and video_queue.boot_id() == ''

    def test_queue_ignores_keys_while_not_serving(self, admin_client, asset, worker_with_token):
        video_queue.stop_for_tests()
        assert video_queue.enqueue((1, 1, 'video')) is False
        with manual_queue():
            assert video_queue.enqueue((1, 1, 'video')) is True
            assert video_queue.enqueue((1, 1, 'video')) is False
