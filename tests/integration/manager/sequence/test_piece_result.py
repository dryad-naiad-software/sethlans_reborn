# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``POST /api/jobs/{id}/piece-result/`` (Unreal
sequence spec FR-SS-4, D12, R7, AC-12, AC-13).

Covers: a missing frame gets 409 naming it; with every frame stored the
piece is DONE and each covered shot gains one R7 timing; shots that do not
match the piece and frame counts that do not add up get 400; unknown keys
at every level; a repeat gets 409; ``gpu`` may be null; timings are capped
at 50 per shot; a PATCH to DONE is refused for worker and admin; an admin
may requeue a failed piece.
"""

import pytest

from workers.models import Job, SequenceShot

from ..unreal_helpers import unreal_asset
from ..unreal_pilot_helpers import JOBS
from .submission_helpers import claimed_piece, piece_body, post_piece, shots_of, upload_all, upload_frame

TIMING_KEYS = {
    'job', 'frames', 'seconds', 's_per_frame', 'startup_s', 'lead_in_s', 'worker', 'gpu_name', 'card_key',
    'vram_peak_mb', 'at',
}


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


@pytest.fixture
def piece(admin_client, asset, worker_with_token):
    worker, client = worker_with_token
    return claimed_piece(admin_client, asset, worker, client)


@pytest.mark.django_db
class TestFinish:

    def test_missing_frame_is_409(self, piece, worker_with_token):
        _worker, client = worker_with_token
        submission, job = piece
        for frame in (0, 1, 2, 4):
            assert upload_frame(client, job, frame).status_code == 200
        resp = post_piece(client, job, piece_body(job))
        assert (resp.status_code, resp.data) == (409, {'error': "Frame 3 of this piece was not uploaded."})
        job.refresh_from_db()
        assert job.status == 'RENDERING'

    def test_done_with_timings(self, piece, worker_with_token):
        worker, client = worker_with_token
        submission, job = piece
        upload_all(client, job)
        resp = post_piece(client, job, piece_body(job, render_s=41.6, warnings=['draft \u0007conflict']))
        assert resp.data == {'status': 'DONE', 'frames': 10}
        job.refresh_from_db()
        assert (job.status, job.render_time_seconds) == ('DONE', 42)
        assert job.completed_at is not None
        assert 'WARNING: draft conflict' in job.last_output
        first, second, third = shots_of(submission)
        assert len(first.timings) == len(second.timings) == 1 and third.timings == []
        timing = second.timings[0]
        assert set(timing) == TIMING_KEYS
        assert (timing['job'], timing['frames'], timing['seconds'], timing['worker']) == (job.pk, 6, 12.0, worker.hostname)
        assert (timing['card_key'], timing['vram_peak_mb']) == ('pci:0000:01:00.0', 9100)
        assert (first.render_time_seconds, second.render_time_seconds) == (8, 12)

    def test_gpu_null_accepted(self, piece, worker_with_token):
        _worker, client = worker_with_token
        _submission, job = piece
        upload_all(client, job)
        resp = post_piece(client, job, piece_body(job, gpu=None, vram_peak_mb=None, startup_s=None))
        assert resp.status_code == 200, resp.data

    def test_repeat_is_409(self, piece, worker_with_token):
        _worker, client = worker_with_token
        _submission, job = piece
        upload_all(client, job)
        assert post_piece(client, job, piece_body(job)).status_code == 200
        assert post_piece(client, job, piece_body(job)).status_code == 409

    def test_timings_capped_at_50(self, piece, worker_with_token):
        _worker, client = worker_with_token
        submission, job = piece
        old = [{'job': n, 'seconds': 1.0} for n in range(50)]
        SequenceShot.objects.filter(submission=submission, index=1).update(timings=old)
        upload_all(client, job)
        assert post_piece(client, job, piece_body(job)).status_code == 200
        timings = shots_of(submission)[0].timings
        assert len(timings) == 50
        assert timings[0]['job'] == 1 and timings[-1]['job'] == job.pk


@pytest.mark.django_db
class TestRefusedBody:

    def test_shots_not_matching(self, piece, worker_with_token):
        _worker, client = worker_with_token
        _submission, job = piece
        upload_all(client, job)
        body = piece_body(job)
        body['shots'] = body['shots'][:1]
        resp = post_piece(client, job, body)
        assert resp.status_code == 400 and "in order: [1, 2]" in resp.data['shots'][0]

    def test_frame_counts_not_adding_up(self, piece, worker_with_token):
        _worker, client = worker_with_token
        _submission, job = piece
        upload_all(client, job)
        body = piece_body(job)
        body['shots'][1]['frames'] = 5
        resp = post_piece(client, job, body)
        assert (resp.status_code, resp.data) == (
            400, {'shots': ["Shot 2 has 6 kept frames in this piece, not 5."]})

    @pytest.mark.parametrize('mutate, name', [
        (lambda body: body.update(extra=1), 'extra'),
        (lambda body: body['gpu'].update(index=0), 'gpu.index'),
        (lambda body: body['shots'][0].update(fps=30), 'fps'),
    ], ids=['top', 'gpu', 'shots'])
    def test_unknown_keys(self, piece, worker_with_token, mutate, name):
        _worker, client = worker_with_token
        _submission, job = piece
        body = piece_body(job)
        mutate(body)
        resp = post_piece(client, job, body)
        assert resp.status_code == 400 and name in resp.data['detail']

    @pytest.mark.parametrize('field, value', [
        ('render_s', '30'), ('render_s', -1), ('vram_peak_mb', 1.5), ('warnings', ['x'] * 33),
        ('warnings', ['x' * 513]), ('summary', 'x' * (32 * 1024 + 1)),
    ], ids=['string-seconds', 'negative', 'float-mb', '33-warnings', 'long-warning', 'long-summary'])
    def test_bounds(self, piece, worker_with_token, field, value):
        _worker, client = worker_with_token
        _submission, job = piece
        resp = post_piece(client, job, piece_body(job, **{field: value}))
        assert resp.status_code == 400 and field in resp.data

    def test_other_worker_is_409(self, piece, make_worker_client):
        _submission, job = piece
        _other, other_client, _token = make_worker_client('otherhost')
        assert post_piece(other_client, job, piece_body(job)).status_code == 409

    def test_unknown_job_is_404(self, db, worker_with_token):
        _worker, client = worker_with_token
        resp = client.post(f'{JOBS}987654/piece-result/', {
            'render_s': 1, 'startup_s': None, 'lead_in_s': None, 'vram_peak_mb': None, 'gpu': None,
            'shots': [{'index': 1, 'frames': 1, 'seconds': 1, 's_per_frame': 1}], 'warnings': [], 'summary': '',
        }, format='json')
        assert resp.status_code == 404


@pytest.mark.django_db
class TestManagerOwnedStatus:

    def test_worker_patch_done_is_400(self, piece, worker_with_token):
        _worker, client = worker_with_token
        _submission, job = piece
        resp = client.patch(f'{JOBS}{job.pk}/', {'status': 'DONE'}, format='json')
        assert (resp.status_code, resp.data) == (
            400, {'detail': f"Piece jobs complete through POST /api/jobs/{job.pk}/piece-result/."})

    def test_admin_patch_done_is_400(self, piece, admin_client):
        _submission, job = piece
        assert admin_client.patch(f'{JOBS}{job.pk}/', {'status': 'DONE'}, format='json').status_code == 400
        job.refresh_from_db()
        assert job.status == 'RENDERING'

    def test_admin_requeue_of_a_failed_piece(self, piece, admin_client, worker_with_token):
        _worker, client = worker_with_token
        submission, job = piece
        resp = client.patch(f'{JOBS}{job.pk}/', {'status': 'ERROR', 'error_message': 'GPU lost'}, format='json')
        assert resp.status_code == 200, resp.data
        resp = admin_client.post(f'{JOBS}{job.pk}/requeue/')
        assert resp.status_code == 200, resp.data
        job.refresh_from_db()
        assert (job.status, job.assigned_worker_id) == ('QUEUED', None)
        assert [s.status for s in shots_of(submission)[:2]] == ['PENDING', 'PENDING']
        assert Job.objects.get(pk=job.pk).unreal_target_sha256 == job.unreal_target_sha256
