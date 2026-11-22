# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: a piece's or sound job's memory report and its
version (Unreal per-machine memory test FR-MT-S-3 step (4), D9, D10,
AC-7, AC-8).

Covers: a ``self`` spill on a piece marks the machine's piece-key result
``does_not_fit`` and adds FR-MT-S-3's warning word for word, once (an
identical retry adds nothing); the remaining pieces are then offered and
claimable only on another machine with a ``fits`` result (AC-7); a
``contention`` report adds its own warning and leaves the result
``fits`` (the remaining pieces stay offered); reports of two machines on
one version keep both warnings; a requeued piece that is squeezed again
adds no second copy; a sound job's spill marks the piece-key result and
closes the sound jobs on that machine; a superseded record (AC-8) and a
driver change still add the warning but change no verdict; at the
64-warning cap the oldest gives way; the card name falls back to the
record's GPU name.
"""

import pytest

from workers.models import Job, SequenceSubmission, UnrealMeasurement

from ..machine_test_helpers import DOES_NOT_FIT, kind_card, pair_record, set_cards
from ..machine_verdict_helpers import NEW_DRIVER
from ..memory_report_helpers import (
    accepted, contention, contention_text, piece_record, report_body, self_spill, spill_text, state,
    warnings_of,
)
from ..unreal_helpers import unreal_asset
from ..unreal_pilot_helpers import JOBS, KIND, claim, ready_worker
from .sequence_helpers import poll_ids
from .sound_helpers import audio_jobs_of, claimed_audio
from .submission_helpers import claimed_piece, pieces_of


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


@pytest.fixture
def two(admin_client, asset, worker_with_token, make_worker_client):
    """Two machines with a fits piece-key result; the first renders piece 0."""
    worker, client = worker_with_token
    other, other_client, _token = make_worker_client('mt-warn-b')
    ready_worker(other)
    submission, piece = claimed_piece(admin_client, asset, worker, client)
    return submission, piece, (worker, client), (other, other_client)


@pytest.mark.django_db
class TestSelfSpill:

    def test_marks_warns_once_and_routes_the_rest_elsewhere(self, two):
        submission, piece, (worker, client), (other, other_client) = two
        record = piece_record(submission, worker)
        body = self_spill(record)
        accepted(client, piece, body, result='does_not_fit')
        assert state(record)[:2] == ('does_not_fit', 'spill')
        assert warnings_of(submission) == [spill_text(worker.hostname, piece.name)]
        accepted(client, piece, body, result='does_not_fit')
        assert len(warnings_of(submission)) == 1
        rest = pieces_of(submission)[1]
        assert rest.pk not in poll_ids(client)
        assert rest.pk in poll_ids(other_client)
        assert claim(client, rest, worker).data == {'error': DOES_NOT_FIT}
        assert claim(other_client, rest, other).status_code == 200

    def test_the_card_name_falls_back_to_the_records_gpu(self, two):
        submission, piece, (worker, client), _other = two
        record = piece_record(submission, worker)
        set_cards(worker, [kind_card(KIND, 0, name='')])
        UnrealMeasurement.objects.filter(pk=record.pk).update(gpu_name='Example GPU')
        accepted(client, piece, self_spill(record), result='does_not_fit')
        assert warnings_of(submission) == [spill_text(worker.hostname, piece.name, card='Example GPU')]


@pytest.mark.django_db
class TestContention:

    def test_warns_and_keeps_fits(self, two):
        submission, piece, (worker, client), _other = two
        record = piece_record(submission, worker)
        accepted(client, piece, contention(record))
        assert state(record)[0] == 'fits'
        assert warnings_of(submission) == [contention_text(worker.hostname, piece.name)]
        assert pieces_of(submission)[1].pk in poll_ids(client)

    def test_two_machines_keep_both_warnings(self, two):
        submission, piece, (worker, client), (other, other_client) = two
        rest = pieces_of(submission)[1]
        assert claim(other_client, rest, other).status_code == 200
        accepted(client, piece, contention(piece_record(submission, worker)))
        accepted(other_client, rest, contention(piece_record(submission, other)))
        assert warnings_of(submission) == [contention_text(worker.hostname, piece.name),
                                           contention_text(other.hostname, rest.name)]

    def test_a_requeued_piece_squeezed_again_adds_no_copy(self, two):
        submission, piece, (worker, client), _other = two
        record = piece_record(submission, worker)
        accepted(client, piece, contention(record))
        resp = client.post(f'{JOBS}{piece.pk}/yield-requeue/', {'reason': 'shutdown'}, format='json')
        assert resp.status_code == 200, resp.data
        assert claim(client, piece, worker).status_code == 200
        accepted(client, piece, contention(record, job_peak_mb=4100))
        assert warnings_of(submission) == [contention_text(worker.hostname, piece.name)]


@pytest.mark.django_db
class TestNoVerdictChangeStillWarns:

    def test_a_superseded_record_warns_and_changes_nothing(self, two, asset):
        submission, piece, (worker, client), _other = two
        record = piece_record(submission, worker)
        UnrealMeasurement.objects.filter(pk=record.pk).update(current=False)
        newer = pair_record(asset, submission.piece_target_sha256, worker, verdict='fits')
        before = (state(record), state(newer))
        accepted(client, piece, self_spill(record))
        assert (state(record), state(newer)) == before
        assert warnings_of(submission) == [spill_text(worker.hostname, piece.name)]

    def test_a_driver_change_warns_and_wins(self, two):
        submission, piece, (worker, client), _other = two
        record = piece_record(submission, worker)
        UnrealMeasurement.objects.filter(pk=record.pk).update(driver_version='31.0.15.5222')
        accepted(client, piece, self_spill(record, driver_version=NEW_DRIVER), result='retest_due')
        assert state(record)[:3] == ('fits', 'pilot', 'driver_changed')
        assert warnings_of(submission) == [spill_text(worker.hostname, piece.name)]

    def test_no_spill_adds_no_warning(self, two):
        submission, piece, (worker, client), _other = two
        accepted(client, piece, report_body(piece_record(submission, worker), spilled=None))
        assert warnings_of(submission) == []


@pytest.mark.django_db
class TestCap:

    def test_the_oldest_warning_gives_way(self, two):
        submission, piece, (worker, client), _other = two
        old = [f'earlier warning {n}' for n in range(64)]
        SequenceSubmission.objects.filter(pk=submission.pk).update(warnings=old)
        accepted(client, piece, contention(piece_record(submission, worker)))
        assert warnings_of(submission) == old[1:] + [contention_text(worker.hostname, piece.name)]


@pytest.mark.django_db
class TestSoundJob:

    def test_a_sound_jobs_spill_marks_the_piece_key_result(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, job = claimed_audio(admin_client, asset, worker, client)
        record = piece_record(submission, worker)
        accepted(client, job, self_spill(record), result='does_not_fit')
        assert state(record)[:2] == ('does_not_fit', 'spill')
        assert warnings_of(submission) == [spill_text(worker.hostname, job.name)]
        rest = [each for each in audio_jobs_of(submission) if each.pk != job.pk]
        assert rest and not {each.pk for each in rest} & set(poll_ids(client))
        assert Job.objects.get(pk=job.pk).status == 'RENDERING'
