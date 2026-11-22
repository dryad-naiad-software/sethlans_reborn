# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the fits-nowhere stop of a version's pieces and sound
jobs (Unreal per-machine memory test D12, D10, FR-MT-N-1/2, AC-11).

The only machine's pilot of the piece key reports ``does_not_fit``
through the real ``pilot-result/``; its on-commit check stops the key.

Covers: every QUEUED piece and every QUEUED audio job of the piece key
become ERROR with the FR-MT-N-2 message (an audio job's size text is the
pieces'), the version's refresh runs (its shots end ERROR), and the
version's ``error_message`` is the message; an audio job RENDERING holds
the stop until it ended, then the next sweep stops the rest.  The version
is refreshed once per stop (two versions of one piece key: once each,
ascending) and gets the message only when it ends ERROR or PARTLY_DONE: a
version whose cancel was asked, or whose pieces are all DONE (only its
sound jobs stop), keeps an empty ``error_message``.  "Render again" of a
shot with no new machine is stopped again by the next sweep; with a new
machine online and not tried, that machine's sweep gives it a pilot and
nothing stops.
"""

from unittest.mock import call

import pytest

from workers.models import Job, SequenceShot, SequenceSubmission
from workers.renderers.unreal_sequence import output_size_text
from workers.services import fits_nowhere

from ..fits_nowhere_helpers import error_of, statuses, stop_text
from ..machine_test_helpers import heartbeat
from ..machine_verdict_helpers import TOTAL, dnf_body
from ..unreal_helpers import unreal_asset
from ..unreal_pilot_helpers import claim, post_result, ready_worker
from .sound_helpers import audio_jobs_of
from .submission_helpers import SUBMISSIONS, done_discovery, pieces_of, submit

CARD = 'NVIDIA GeForce RTX 5090'
PIECE_SIZE = "at 64x36 (the preset's size)"


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


@pytest.fixture
def version(admin_client, asset, worker_with_token):
    """A version with sound and the only machine's pilot of its piece key RENDERING on that machine."""
    worker, client = worker_with_token
    done_discovery(admin_client, asset, worker, client)
    submission, _ = submit(admin_client, asset, sound=True)
    pilot = Job.objects.get(unreal_kind='pilot', target_worker=worker, asset=asset)
    assert pilot.unreal_target_sha256 == submission.piece_target_sha256
    assert claim(client, pilot, worker).status_code == 200
    return submission, pilot, worker, client


def _does_not_fit(client, pilot, capture):
    with capture(execute=True):
        resp = post_result(client, pilot.pk, dnf_body())
    assert resp.status_code == 200, resp.data


def _jobs(submission):
    return pieces_of(submission) + audio_jobs_of(submission)


def _text(host):
    return stop_text(CARD, TOTAL, host, 8102, PIECE_SIZE)


def _rerender(admin_client, submission, index=1):
    resp = admin_client.post(f'{SUBMISSIONS}{submission.pk}/shots/{index}/rerender/', format='json')
    assert resp.status_code == 200 and resp.data['requeued'] == 1, resp.data


@pytest.mark.django_db
class TestStop:

    def test_pieces_and_sound_jobs_stop_and_the_version_follows(self, version, django_capture_on_commit_callbacks):
        submission, pilot, worker, client = version
        jobs = _jobs(submission)
        assert len(jobs) == 4 and statuses(*jobs) == ['QUEUED'] * 4
        assert output_size_text(jobs[0]) == PIECE_SIZE
        _does_not_fit(client, pilot, django_capture_on_commit_callbacks)
        assert statuses(*jobs) == ['ERROR'] * 4
        assert [error_of(job) for job in jobs] == [_text(worker.hostname)] * 4
        submission.refresh_from_db()
        assert (submission.status, submission.error_message) == ('ERROR', _text(worker.hostname))
        assert set(SequenceShot.objects.filter(submission=submission).values_list('status', flat=True)) == {'ERROR'}

    def test_a_sound_job_rendering_holds_the_stop_until_it_ended(self, version, django_capture_on_commit_callbacks):
        submission, pilot, worker, client = version
        sound, other_sound = audio_jobs_of(submission)
        Job.objects.filter(pk=sound.pk).update(status='RENDERING', assigned_worker=worker)
        _does_not_fit(client, pilot, django_capture_on_commit_callbacks)
        assert statuses(*pieces_of(submission), other_sound) == ['QUEUED'] * 3
        Job.objects.filter(pk=sound.pk).update(status='DONE')
        heartbeat(client, worker, django_capture_on_commit_callbacks)
        assert statuses(*pieces_of(submission), other_sound, sound) == ['ERROR'] * 3 + ['DONE']
        assert error_of(other_sound) == _text(worker.hostname)


@pytest.mark.django_db
class TestVersionRefresh:

    @pytest.fixture
    def spy(self, mocker):
        return mocker.patch.object(fits_nowhere, 'refresh_submission', wraps=fits_nowhere.refresh_submission)

    def test_one_refresh_per_version_ascending(self, admin_client, asset, version, spy,
                                               django_capture_on_commit_callbacks):
        submission, pilot, _worker, client = version
        second, _ = submit(admin_client, asset, sound=True)
        assert second.piece_target_sha256 == submission.piece_target_sha256
        _does_not_fit(client, pilot, django_capture_on_commit_callbacks)
        assert statuses(*_jobs(submission), *_jobs(second)) == ['ERROR'] * 8
        assert spy.call_args_list == [call(submission.pk), call(second.pk)]

    def test_a_version_whose_cancel_was_asked_keeps_no_message(self, version, spy,
                                                               django_capture_on_commit_callbacks):
        submission, pilot, _worker, client = version
        SequenceSubmission.objects.filter(pk=submission.pk).update(cancel_requested=True)
        _does_not_fit(client, pilot, django_capture_on_commit_callbacks)
        assert statuses(*_jobs(submission)) == ['ERROR'] * 4
        submission.refresh_from_db()
        assert (submission.status, submission.error_message) == ('CANCELED', '')
        assert spy.call_args_list == [call(submission.pk)]

    def test_only_sound_jobs_left_stop_and_a_done_version_keeps_no_message(
            self, version, django_capture_on_commit_callbacks):
        submission, pilot, worker, client = version
        Job.objects.filter(pk__in=[piece.pk for piece in pieces_of(submission)]).update(status='DONE')
        _does_not_fit(client, pilot, django_capture_on_commit_callbacks)
        sounds = audio_jobs_of(submission)
        assert statuses(*sounds) == ['ERROR', 'ERROR']
        assert [error_of(job) for job in sounds] == [_text(worker.hostname)] * 2
        submission.refresh_from_db()
        assert (submission.status, submission.error_message) == ('DONE', '')


@pytest.mark.django_db
class TestRenderAgain:

    def test_with_no_new_machine_the_next_sweep_stops_it_again(self, admin_client, version,
                                                               django_capture_on_commit_callbacks):
        submission, pilot, worker, client = version
        _does_not_fit(client, pilot, django_capture_on_commit_callbacks)
        _rerender(admin_client, submission)
        first = pieces_of(submission)[0]
        assert statuses(first) == ['QUEUED']
        heartbeat(client, worker, django_capture_on_commit_callbacks)
        assert statuses(first) == ['ERROR']
        assert error_of(first) == _text(worker.hostname)
        assert SequenceSubmission.objects.values_list('error_message', flat=True).get(
            pk=submission.pk) == _text(worker.hostname)

    def test_a_new_machine_not_tried_gets_a_pilot_instead(self, admin_client, version, make_worker_client,
                                                          django_capture_on_commit_callbacks):
        submission, pilot, worker, client = version
        _does_not_fit(client, pilot, django_capture_on_commit_callbacks)
        larger, larger_client, _token = make_worker_client('mt-fn-larger')
        ready_worker(larger, total_mb=49140)
        _rerender(admin_client, submission)
        first = pieces_of(submission)[0]
        heartbeat(client, worker, django_capture_on_commit_callbacks)
        assert statuses(first) == ['QUEUED']
        heartbeat(larger_client, larger, django_capture_on_commit_callbacks)
        [new_pilot] = Job.objects.filter(unreal_kind='pilot', target_worker=larger)
        assert (new_pilot.status, new_pilot.unreal_target_sha256) == ('QUEUED', submission.piece_target_sha256)
        heartbeat(client, worker, django_capture_on_commit_callbacks)
        assert statuses(first) == ['QUEUED']
