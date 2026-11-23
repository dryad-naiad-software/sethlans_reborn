# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Issue #294: builds queued at server start while the parts check still runs.

The serving process no longer waits for the parts check before its build
queue starts.  A build taken while FFmpeg's status is still ``installing``
used to fail for good ("FFmpeg is not available").  Now it stays PENDING,
and once the check ends ``parts_check_startup`` queues it again, so it is
built with FFmpeg's real status.  The check here is a stand-in part that
blocks on an Event (no FFmpeg lookup, no network); the re-queue body runs
in the test's thread because the in-memory test database is not shared
with other threads.
"""

import threading
from unittest import mock

import pytest

from workers import parts_check_startup, video_queue, video_startup
from workers.models import SequenceShot
from workers.services.parts_check import registry

from ..unreal_helpers import unreal_asset
from .build_helpers import RUN_FFMPEG, FakeFfmpeg, finish_pieces, version
from .submission_helpers import VIDEO, ffmpeg_ready

pytestmark = pytest.mark.django_db


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


@pytest.fixture(autouse=True)
def _stopped_queue():
    video_queue.stop_for_tests()
    registry._reset_for_tests()
    yield
    video_queue.stop_for_tests()
    registry._reset_for_tests()


@pytest.fixture
def slow_check(monkeypatch):
    """An FFmpeg part that reports ready once ``release`` is set."""
    release = threading.Event()
    monkeypatch.setattr(registry, '_parts', {
        'ffmpeg': lambda: (release.wait(10), registry.Status(status='ready', path='ffmpeg'))[1],
    })
    monkeypatch.setattr(registry, '_statuses', {'ffmpeg': registry.Status()})
    yield release
    release.set()


def _shot_video(submission, index=1):
    return SequenceShot.objects.filter(submission=submission, index=index).values_list(
        'video_status', 'video_error').get()


def test_build_waits_pending_during_the_check_and_runs_after(
        admin_client, asset, worker_with_token, slow_check, django_capture_on_commit_callbacks):
    worker, client = worker_with_token
    with ffmpeg_ready():  # the create guard needs FFmpeg ready
        submission = version(admin_client, asset, worker, client, video_settings=VIDEO, sound=False)
        finish_pieces(client, worker, submission)
    SequenceShot.objects.filter(submission=submission, index=1).update(
        video_status='ASSEMBLING', assembly_boot='old-boot')

    registry.run_parts_check()  # what start_for_server starts; blocked on slow_check
    assert registry.get_status('ffmpeg').status == 'installing'
    with mock.patch.object(video_queue, 'start', side_effect=video_queue.start_manual_for_tests), \
            mock.patch('workers.services.sequence_discovery.settle_open_discoveries', return_value=0), \
            mock.patch(RUN_FFMPEG, FakeFfmpeg()), \
            mock.patch.object(parts_check_startup, 'connections'):
        with django_capture_on_commit_callbacks(execute=True):
            video_startup.on_server_start()
        with django_capture_on_commit_callbacks(execute=True):
            assert video_queue.run_pending_for_tests() >= 1
        # Before the fix this was ('ERROR', 'FFmpeg is not available on this system.').
        assert _shot_video(submission) == ('PENDING', '')

        slow_check.set()
        assert registry.wait_for_checks(5) is True
        assert registry.get_status('ffmpeg').status == 'ready'
        with django_capture_on_commit_callbacks(execute=True):
            parts_check_startup._requeue_when_checked()
        with django_capture_on_commit_callbacks(execute=True):
            assert video_queue.run_pending_for_tests() >= 1
    assert _shot_video(submission) == ('DONE', '')
