# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the R8 ``routing_note`` of Unreal jobs (Unreal foundation
FR-RT-9, FR-T-3, AC-7).  Per-machine memory test D7: whether a render fits
is each machine's own result, so the note no longer estimates a farm-wide
memory need from card sizes (no 15% margin, no pool caps, no pilot minimum).
FR-MT-U-2: the memory part follows the online machines' own pairs; an
online machine with a fits result gives no note, whatever the other cards.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from workers.models import Job, JobStatus, Worker

from .unreal_helpers import (
    BUILD, RENDER_SETTINGS, SHARE, caps, card_key_of, gpu_memory, set_share, unreal_asset, unreal_project,
)
from .unreal_pilot_helpers import measure_key, ready_worker

# A peak that the removed farm-wide estimate (peak + 15%) put over a 16 GB card.
PEAK_MB = 14000

URL = '/api/jobs/'
NO_SHARE = "The Sethlans share is not configured."
NO_BUILD = f"No online worker reports Unreal Engine build {BUILD}."
NO_REACH = (f"No online worker with Unreal Engine build {BUILD} is configured "
            "with the manager's Sethlans share and reports it reachable.")
NO_MACHINE = (f"No online worker with Unreal Engine build {BUILD} has a ticked graphics card "
              "with a live memory reading.")


def _worker(hostname, capabilities, memory=None, online=True):
    worker = Worker.objects.create(hostname=hostname, is_active=True, unreal_capabilities=capabilities,
                                   gpu_memory=memory or {})
    if not online:
        Worker.objects.filter(pk=worker.pk).update(last_seen=timezone.now() - timedelta(seconds=600))
    return worker


def _fitting_machine(job):
    """An online, eligible machine with a fits result of the job's key (FR-MT-U-2: no note)."""
    worker = ready_worker(_worker('note-fits', caps()))
    measure_key(job, peak_mb=PEAK_MB, worker=worker)
    return worker


@pytest.fixture
def job(db):
    project = unreal_project(unreal_cvar_overrides={'r.Streaming.PoolSize': '9000',
                                                    'r.RayTracing.ResidentGeometryMemoryPoolSizeInMB': '2048'})
    job = Job.objects.create(name='NoteJob', asset=unreal_asset(project), output_file_pattern='//r/f.png',
                             render_settings=RENDER_SETTINGS, render_device='GPU')
    # Per-machine D2: one (offline) machine's fits result measures the key.
    measure_key(job, peak_mb=PEAK_MB, worker=_worker('note-tester', caps(), online=False))
    return job


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

    def test_small_cards_give_no_memory_note(self, admin_client, job):
        """D7: no farm-wide estimate; the old peak + 15% rule refused these cards."""
        set_share(SHARE)
        _worker('note-16', caps(), gpu_memory(16303))
        _worker('note-mixed', caps(), gpu_memory(16303, 12282))
        # FR-MT-U-2: none of them has a ticked card kind, so none can test the key.
        assert _note(admin_client, job) == NO_MACHINE
        _fitting_machine(job)
        assert _note(admin_client, job) is None

    def test_enough_memory_on_the_largest_gpu(self, admin_client, job):
        set_share(SHARE)
        _worker('note-16', caps(), gpu_memory(16303))
        _worker('note-32', caps(), gpu_memory(32607, 32607, used=30000))
        _fitting_machine(job)
        assert _note(admin_client, job) is None

    def test_worker_without_gpu_memory_is_not_counted_against(self, admin_client, job):
        set_share(SHARE)
        _worker('note-16', caps(), gpu_memory(16303))
        _worker('note-old', caps())
        _fitting_machine(job)
        assert _note(admin_client, job) is None

    def test_a_pilot_has_no_memory_need(self, admin_client):
        """S2, D7: the 12,478 MB pilot minimum is gone."""
        set_share(SHARE)
        job = Job.objects.create(name='NoteDefault', asset=unreal_asset(name='A2'), output_file_pattern='//r/f.png',
                                 unreal_kind='pilot')
        _worker('note-12', caps(), gpu_memory(12282))
        assert _note(admin_client, job) is None


def _without_total(memory, index):
    """``memory`` plus one GPU entry that reports no ``total_mb``."""
    memory['gpus'].append({'index': index, 'name': 'GPU', 'used_mb': 0, 'sethlans_used_mb': None,
                           'other_used_mb': None, 'card_key': card_key_of(index)})
    return memory


@pytest.mark.django_db
class TestNoFarmMemoryEstimate:
    """D7 replaces AC-25 / FR-GH-9's largest-card estimate: card sizes never make a note."""

    @pytest.mark.parametrize('make_memory', [
        lambda: gpu_memory(12282, 32607), lambda: gpu_memory(8188, 12282), lambda: gpu_memory(16303),
        lambda: gpu_memory(32607), lambda: _without_total(gpu_memory(12282), 1),
        lambda: _without_total(gpu_memory(), 0), gpu_memory,
    ], ids=['mixed-large', 'mixed-small', 'one-16g', 'one-32g', 'partial', 'no-totals', 'empty-reading'])
    def test_a_measured_key_has_no_memory_note(self, admin_client, job, make_memory):
        set_share(SHARE)
        _worker('note-cards', caps(), make_memory())
        _fitting_machine(job)
        assert _note(admin_client, job) is None


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
