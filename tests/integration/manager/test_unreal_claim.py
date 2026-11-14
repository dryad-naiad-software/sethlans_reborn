# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: claiming Unreal jobs (Unreal foundation FR-RT-4, FR-RT-5,
FR-MS-5, FR-T-3, AC-6).
"""

import pytest

from workers.models import Job, JobStatus

from .unreal_helpers import BUILD, SHARE, caps, set_share, unreal_asset


@pytest.fixture
def job(db):
    return Job.objects.create(name='ClaimUnreal', asset=unreal_asset(), output_file_pattern='//r/u.png')


def _claim(client, job, worker):
    return client.post(f'/api/jobs/{job.pk}/claim/', {'worker_id': worker.pk}, format='json')


def _ready(worker, **kwargs):
    set_share(SHARE)
    worker.unreal_capabilities = caps(**kwargs)
    worker.save(update_fields=['unreal_capabilities'])


@pytest.mark.django_db
class TestClaimChecks:

    def test_without_the_build(self, worker_with_token, job):
        worker, client = worker_with_token
        _ready(worker, builds=('5.8.4-1',))
        resp = _claim(client, job, worker)
        assert (resp.status_code, resp.data) == (409, {'error': f"Worker does not report Unreal Engine build {BUILD}."})

    def test_share_unset(self, worker_with_token, job):
        worker, client = worker_with_token
        _ready(worker)
        set_share('')
        resp = _claim(client, job, worker)
        assert (resp.status_code, resp.data) == (409, {'error': "The Sethlans share is not configured."})

    @pytest.mark.parametrize('share', [r'\\other.lan\share', '', r'\\alpha\mestrella\applications\unreal'])
    def test_share_mismatch(self, worker_with_token, job, share):
        worker, client = worker_with_token
        _ready(worker, share=share)
        resp = _claim(client, job, worker)
        assert (resp.status_code, resp.data) == (
            409, {'error': "Worker's Sethlans share does not match the manager's setting."},
        )

    def test_share_unreachable(self, worker_with_token, job):
        worker, client = worker_with_token
        _ready(worker, reachable=False)
        resp = _claim(client, job, worker)
        assert (resp.status_code, resp.data) == (
            409, {'error': "Worker does not report the Sethlans share as reachable."},
        )

    @pytest.mark.parametrize('share', [SHARE, SHARE.upper(), SHARE + '\\', r'\\alpha.lan\MESTRELLA\Applications\Unreal'])
    def test_success(self, worker_with_token, job, share):
        worker, client = worker_with_token
        _ready(worker, share=share)
        resp = _claim(client, job, worker)
        assert resp.status_code == 200, resp.data
        job.refresh_from_db()
        assert (job.status, job.assigned_worker_id) == (JobStatus.RENDERING, worker.pk)
        assert resp.data['routing_note'] is None


@pytest.mark.django_db
class TestIdempotentReclaim:

    def test_assignee_reclaim_is_200_with_no_change(self, worker_with_token, make_worker_client, job):
        worker, client = worker_with_token
        _ready(worker)
        assert _claim(client, job, worker).status_code == 200
        before = Job.objects.values().get(pk=job.pk)
        resp = _claim(client, job, worker)
        assert resp.status_code == 200, resp.data
        assert Job.objects.values().get(pk=job.pk) == before
        other, other_client, _token = make_worker_client('claim-other')
        _ready(other)
        resp = _claim(other_client, job, other)
        assert (resp.status_code, resp.data) == (409, {'error': "Job is not available for claiming."})

    def test_blender_reclaim_keeps_409(self, worker_with_token, asset):
        worker, client = worker_with_token
        blender = Job.objects.create(name='ClaimBlender', asset=asset, output_file_pattern='//r/b.png')
        assert _claim(client, blender, worker).status_code == 200
        resp = _claim(client, blender, worker)
        assert (resp.status_code, resp.data) == (409, {'error': "Job is not available for claiming."})
