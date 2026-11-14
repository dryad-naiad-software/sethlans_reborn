# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the R8 ``routing_note`` of Unreal jobs (Unreal foundation
FR-RT-9, FR-T-3, AC-7).
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from workers.models import Job, JobStatus, Worker

from .unreal_helpers import BUILD, RENDER_SETTINGS, SHARE, caps, gpu_memory, set_share, unreal_asset, unreal_project

URL = '/api/jobs/'
NO_SHARE = "The Sethlans share is not configured."
NO_BUILD = f"No online worker reports Unreal Engine build {BUILD}."
NO_REACH = (f"No online worker with Unreal Engine build {BUILD} is configured "
            "with the manager's Sethlans share and reports it reachable.")


def _no_vram(need):
    return f"No online worker with Unreal Engine build {BUILD} has enough GPU memory (needs {need} MB)."


def _worker(hostname, capabilities, memory=None, online=True):
    worker = Worker.objects.create(hostname=hostname, is_active=True, unreal_capabilities=capabilities,
                                   gpu_memory=memory or {})
    if not online:
        Worker.objects.filter(pk=worker.pk).update(last_seen=timezone.now() - timedelta(seconds=600))
    return worker


@pytest.fixture
def job(db):
    project = unreal_project(unreal_cvar_overrides={'r.Streaming.PoolSize': '9000',
                                                    'r.RayTracing.ResidentGeometryMemoryPoolSizeInMB': '2048'})
    return Job.objects.create(name='NoteJob', asset=unreal_asset(project), output_file_pattern='//r/f.png',
                              render_settings=RENDER_SETTINGS, render_device='GPU')


def _note(client, job):
    resp = client.get(f'{URL}{job.pk}/')
    assert resp.status_code == 200, resp.data
    return resp.data['routing_note']


@pytest.mark.django_db
class TestMessages:

    def test_share_unset(self, admin_client, job):
        _worker('note-w', caps())
        assert _note(admin_client, job) == NO_SHARE

    def test_no_online_build(self, admin_client, job):
        set_share(SHARE)
        _worker('note-other-build', caps(builds=('5.8.4-1',)))
        _worker('note-offline', caps(), online=False)
        _worker('note-disabled', caps(enabled=False))
        assert _note(admin_client, job) == NO_BUILD

    @pytest.mark.parametrize('kwargs', [{'share': r'\\other.lan\x'}, {'reachable': False}])
    def test_no_worker_reaches_the_share(self, admin_client, job, kwargs):
        set_share(SHARE)
        _worker('note-w', caps(**kwargs))
        assert _note(admin_client, job) == NO_REACH

    def test_not_enough_gpu_memory(self, admin_client, job):
        set_share(SHARE)
        _worker('note-16', caps(), gpu_memory(16303))
        _worker('note-mixed', caps(), gpu_memory(32607, 12282))
        assert _note(admin_client, job) == _no_vram(20126)

    def test_enough_memory_on_every_gpu(self, admin_client, job):
        set_share(SHARE)
        _worker('note-16', caps(), gpu_memory(16303))
        _worker('note-32', caps(), gpu_memory(32607, 32607, used=30000))
        assert _note(admin_client, job) is None

    def test_worker_without_gpu_memory_is_not_counted_against(self, admin_client, job):
        set_share(SHARE)
        _worker('note-16', caps(), gpu_memory(16303))
        _worker('note-old', caps())
        assert _note(admin_client, job) is None

    def test_default_pools_without_overrides(self, admin_client):
        set_share(SHARE)
        job = Job.objects.create(name='NoteDefault', asset=unreal_asset(name='A2'), output_file_pattern='//r/f.png')
        _worker('note-12', caps(), gpu_memory(12282))
        assert _note(admin_client, job) == _no_vram(12478)


@pytest.mark.django_db
class TestWhenAbsent:

    def test_null_unless_queued(self, admin_client, job):
        Job.objects.filter(pk=job.pk).update(status=JobStatus.DONE)
        assert _note(admin_client, job) is None

    def test_null_for_workers(self, worker_with_token, job):
        _worker_row, client = worker_with_token
        assert _note(client, job) is None

    def test_null_for_blender(self, admin_client, asset):
        blender = Job.objects.create(name='NoteBlender', asset=asset, output_file_pattern='//r/f.png')
        assert _note(admin_client, blender) is None


@pytest.mark.django_db
class TestQueryCount:

    def _count(self, client, **params):
        with CaptureQueriesContext(connection) as ctx:
            resp = client.get(URL, params)
        assert resp.status_code == 200
        return len(ctx.captured_queries), resp

    def test_twenty_unreal_jobs_add_at_most_two_queries(self, admin_client, asset):
        set_share(SHARE)
        _worker('note-q', caps(), gpu_memory(16303))
        shared = unreal_asset()
        Job.objects.create(name='Q0', asset=shared, output_file_pattern='//r/f.png')
        one, _ = self._count(admin_client, renderer='UNREAL')
        for i in range(1, 20):
            Job.objects.create(name=f'Q{i}', asset=shared, output_file_pattern='//r/f.png')
        many, resp = self._count(admin_client, renderer='UNREAL')
        assert len(resp.data) == 20
        assert many == one
        blender_only, _ = self._count(admin_client, renderer='BLENDER')
        Job.objects.create(name='QB', asset=asset, output_file_pattern='//r/f.png')
        with_rows, _ = self._count(admin_client, renderer='BLENDER')
        assert with_rows == blender_only

    def test_unreal_rows_add_two_queries_over_blender_rows(self, admin_client, asset):
        set_share(SHARE)
        Job.objects.create(name='B1', asset=asset, output_file_pattern='//r/f.png')
        blender, _ = self._count(admin_client, renderer='BLENDER')
        Job.objects.create(name='U1', asset=unreal_asset(), output_file_pattern='//r/f.png')
        unreal, _ = self._count(admin_client, renderer='UNREAL')
        assert unreal - blender <= 2
