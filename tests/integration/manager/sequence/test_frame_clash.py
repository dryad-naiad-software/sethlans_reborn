# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the frame clash (Unreal sequence spec FR-SS-2,
FR-SS-5, chunk 3's review note for chunk 7).

Once a shot is rendered again beside a piece still running, the same frame
can arrive from two jobs.  Covers: the frame of the job that started later
is kept; an upload from the older job is dropped with 200 and
``kept_newer`` (its file removed, so the worker goes on); a newer job
replaces an older job's frame; the unique-key race (both inserts see no
row) keeps the same rule; a deleted holder never wins.
"""

import os
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from workers.models import Job, SequenceFrame
from workers.services import sequence_frames

from ..unreal_helpers import unreal_asset
from .submission_helpers import claimed_piece, upload_frame

pytestmark = pytest.mark.django_db


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return settings.MEDIA_ROOT


@pytest.fixture
def two_jobs(admin_client, media, worker_with_token):
    """The claimed first piece and a second running job over the same frames, claimed one minute later."""
    worker, client = worker_with_token
    _submission, older = claimed_piece(admin_client, unreal_asset(), worker, client)
    started = timezone.now() - timedelta(minutes=5)
    Job.objects.filter(pk=older.pk).update(started_at=started)
    newer = Job.objects.get(pk=older.pk)
    newer.pk, newer.id, newer.name = None, None, 'sethlans-seq-rerun-p1'
    newer.started_at = started + timedelta(minutes=1)
    newer.save()
    older.refresh_from_db()
    return client, older, newer


def _row(frame=3):
    return SequenceFrame.objects.get(frame_number=frame)


def _ok(resp):
    assert resp.status_code == 200, resp.data
    return resp.data


class TestNewerJobWins:

    def test_older_upload_after_the_newer_is_dropped(self, two_jobs, media, django_capture_on_commit_callbacks):
        client, older, newer = two_jobs
        assert _ok(upload_frame(client, newer, 3))['replaced'] is False
        kept = _row().file.name
        with django_capture_on_commit_callbacks(execute=True):
            data = _ok(upload_frame(client, older, 3))
        assert (data['replaced'], data['kept_newer']) == (False, True)
        assert (_row().job_id, _row().file.name) == (newer.pk, kept)
        shot_dir = os.path.dirname(os.path.join(media, kept))
        assert os.listdir(shot_dir) == [os.path.basename(kept)]

    def test_newer_upload_replaces_the_older(self, two_jobs, media, django_capture_on_commit_callbacks):
        client, older, newer = two_jobs
        _ok(upload_frame(client, older, 3))
        old = _row().file.name
        with django_capture_on_commit_callbacks(execute=True):
            data = _ok(upload_frame(client, newer, 3))
        assert (data['replaced'], data['kept_newer']) == (True, False)
        assert _row().job_id == newer.pk and not os.path.exists(os.path.join(media, old))

    def test_same_job_repeat_still_replaces(self, two_jobs):
        client, older, _newer = two_jobs
        _ok(upload_frame(client, older, 3))
        assert _ok(upload_frame(client, older, 3))['replaced'] is True

    def test_deleted_holder_never_wins(self, two_jobs):
        client, older, newer = two_jobs
        _ok(upload_frame(client, newer, 3))
        SequenceFrame.objects.update(job=None)
        data = _ok(upload_frame(client, older, 3))
        assert data['replaced'] is True and _row().job_id == older.pk


class _FirstMiss:
    """A locked frame query whose first ``first()`` sees no row, as when both inserts race."""

    calls = 0

    def __init__(self, queryset):
        self.queryset = queryset

    def filter(self, **kwargs):
        return _FirstMiss(self.queryset.filter(**kwargs))

    def first(self):
        _FirstMiss.calls += 1
        return None if _FirstMiss.calls == 1 else self.queryset.first()


def _racing():
    real = sequence_frames.lock_own_rows
    _FirstMiss.calls = 0

    def locked(manager, *related):
        rows = real(manager, *related)
        return _FirstMiss(rows) if manager.model is SequenceFrame else rows
    return mock.patch.object(sequence_frames, 'lock_own_rows', side_effect=locked)


class TestUniqueKeyRace:

    def test_losing_insert_from_the_older_job_keeps_the_newer_frame(self, two_jobs):
        client, older, newer = two_jobs
        _ok(upload_frame(client, newer, 3))
        with _racing():
            data = _ok(upload_frame(client, older, 3))
        assert data['kept_newer'] is True and _row().job_id == newer.pk
        assert SequenceFrame.objects.count() == 1

    def test_losing_insert_from_the_newer_job_replaces(self, two_jobs):
        client, older, newer = two_jobs
        _ok(upload_frame(client, older, 3))
        with _racing():
            data = _ok(upload_frame(client, newer, 3))
        assert data['replaced'] is True and _row().job_id == newer.pk
        assert SequenceFrame.objects.count() == 1


class TestOrder:

    def test_unstarted_holder_is_older(self, two_jobs):
        _client, older, newer = two_jobs
        assert sequence_frames.holder_is_newer(newer.pk, older) is True
        Job.objects.filter(pk=newer.pk).update(started_at=None)
        assert sequence_frames.holder_is_newer(newer.pk, older) is False
        assert sequence_frames.holder_is_newer(None, older) is False
        assert sequence_frames.holder_is_newer(older.pk, older) is False
