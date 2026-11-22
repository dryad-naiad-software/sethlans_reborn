# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: a sequence version's ``machines`` (Unreal per-machine
memory test FR-MT-U-1, NF-3, chunk 8) and the audio job's
``unreal_machines`` (D10: the piece key).

Covers: the detail lists the piece key's machines (the planned pilot's
``testing`` row, then the result); null while the version reads its
shots; the list has no ``machines``; the audio job detail shows the same
rows as its version; the shots job shows null; a worker token is refused
the endpoint and the serializer gives it null; the version list costs the
same for 1 and 20 versions with keys of their own and records; the detail
costs the same for one machine and four.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from workers.models import SequenceSubmission
from workers.serializers.machine_rows import submission_machines

from ..fits_nowhere_helpers import dnf_record
from ..machine_rows_helpers import WAITING, machine, worker_context
from ..machine_test_helpers import pair_record
from ..unreal_helpers import unreal_asset
from ..unreal_pilot_helpers import JOBS, KIND, ready_worker
from .sequence_helpers import shots_job_of
from .sound_helpers import audio_jobs_of
from .submission_helpers import SUBMISSIONS, done_discovery, measure_submission, pieces_of, submit

pytestmark = pytest.mark.django_db


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


@pytest.fixture
def shots_read(admin_client, asset, worker_with_token):
    """Shots read; the token worker is the one online eligible machine."""
    worker, client = worker_with_token
    done_discovery(admin_client, asset, worker, client)
    return ready_worker(worker), client


def _detail(client, submission):
    resp = client.get(f'{SUBMISSIONS}{submission.pk}/')
    assert resp.status_code == 200, resp.data
    return resp.data


def _states(rows):
    return [(row['hostname'], row['card_kind'], row['state']) for row in rows]


class TestDetail:

    def test_lists_the_piece_keys_machines(self, admin_client, asset, shots_read):
        worker, _client = shots_read
        submission, _ = submit(admin_client, asset, sound=False)
        assert submission.piece_target_sha256
        assert _states(_detail(admin_client, submission)['machines']) == [('inthost', KIND, 'testing')]
        measure_submission(asset, peak_mb=9100, worker=worker, submission=submission)
        [row] = _detail(admin_client, submission)['machines']
        assert (row['worker'], row['state'], row['peak_mb'], row['online']) == (worker.pk, 'fits', 9100, True)

    def test_null_while_reading_the_shots(self, admin_client, asset, worker_with_token):
        submission, _ = submit(admin_client, asset)
        assert (submission.status, submission.piece_target_sha256) == ('DISCOVERING', '')
        assert _detail(admin_client, submission)['machines'] is None

    def test_the_list_has_no_machines(self, admin_client, asset, shots_read):
        submit(admin_client, asset, sound=False)
        [row] = admin_client.get(SUBMISSIONS).data['results']
        assert 'machines' not in row


class TestJobsOfTheVersion:

    def test_audio_job_shows_the_piece_keys_rows(self, admin_client, asset, shots_read):
        worker, _client = shots_read
        submission, _ = submit(admin_client, asset, sound=True)
        # Online after planning, so the version made no pilot for it.
        other = machine('host-other')
        dnf_record(asset, submission.piece_target_sha256, other)
        audio = audio_jobs_of(submission)[0]
        rows = admin_client.get(f'{JOBS}{audio.pk}/').data['unreal_machines']
        assert rows == _detail(admin_client, submission)['machines']
        assert _states(rows) == [('host-other', KIND, 'does_not_fit'), ('inthost', KIND, 'testing')]
        piece = pieces_of(submission)[0]
        assert admin_client.get(f'{JOBS}{piece.pk}/').data['unreal_machines'] == rows
        assert worker.pk in [row['worker'] for row in rows]

    def test_shots_job_is_null(self, admin_client, asset, worker_with_token):
        submission, _ = submit(admin_client, asset)
        job = shots_job_of(submission.discovery)
        assert admin_client.get(f'{JOBS}{job.pk}/').data['unreal_machines'] is None


class TestWorkerToken:

    def test_refused_and_null(self, admin_client, asset, shots_read):
        worker, client = shots_read
        submission, _ = submit(admin_client, asset, sound=False)
        assert client.get(f'{SUBMISSIONS}{submission.pk}/').status_code == 403
        assert submission_machines(worker_context(worker.user), submission) is None
        assert len(submission_machines({}, submission)) == 1


class TestQueryCounts:

    def test_list_is_flat_with_keys_of_their_own(self, admin_client, asset, shots_read):
        submit(admin_client, asset, sound=True, resolution_x=64, resolution_y=64)
        with CaptureQueriesContext(connection) as one:
            rows = admin_client.get(SUBMISSIONS).data['results']
        assert rows[0]['routing_note'] == WAITING.format(n=1)
        for n in range(1, 20):
            submit(admin_client, asset, sound=True, resolution_x=64 + 2 * n, resolution_y=64)
        # Online after planning (no pilots of its own): does not fit every other key, untried on the rest.
        other = machine('host-other')
        keys = list(SequenceSubmission.objects.order_by('pk').values_list('piece_target_sha256', flat=True))
        assert len(set(keys)) == 20
        for key in keys[1::2]:
            dnf_record(asset, key, other)
        with CaptureQueriesContext(connection) as twenty:
            rows = admin_client.get(SUBMISSIONS).data['results']
        assert len(rows) == 20 and {row['routing_note'] for row in rows} == {
            WAITING.format(n=1), WAITING.format(n=2)}
        assert len(twenty.captured_queries) == len(one.captured_queries)

    def test_detail_is_flat_with_machines(self, admin_client, asset, shots_read):
        submission, _ = submit(admin_client, asset, sound=False)
        with CaptureQueriesContext(connection) as one:
            assert len(_detail(admin_client, submission)['machines']) == 1
        for n in range(3):
            worker = machine(f'host-{n}')
            pair_record(asset, submission.piece_target_sha256, worker, pilot='ERROR', error='boom')
        with CaptureQueriesContext(connection) as four:
            data = _detail(admin_client, submission)
        assert [row['state'] for row in data['machines']].count('failed') == 3
        assert len(four.captured_queries) == len(one.captured_queries)
