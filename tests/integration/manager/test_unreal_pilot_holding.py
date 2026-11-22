# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: unmeasured normal jobs are held, pilots are routed
(Unreal foundation FR-MS-5, FR-RT-4, FR-RT-9, FR-PM-1, FR-PM-5, AC-7,
AC-46, AC-49).
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from django.db import transaction

from workers.models import Job, Worker
from workers.services.unreal_pilot import Target, create_pilot, lock_project

from .unreal_helpers import RENDER_SETTINGS, SHARE, caps, gpu_memory, set_share, unreal_asset, unreal_project
from .unreal_pilot_helpers import (
    JOBS, KIND, POLL, claim, created_job, measure, pilots, ready_worker, record_of,
)

# Per-machine FR-MT-U-2: the one online machine has not tested the key yet.
PENDING = "Waiting for memory tests on 1 machine(s)."
# Per-machine FR-MT-T-2: the claim judges the caller's own pair.
NOT_TESTED = "This machine has not tested this render yet."
CVARS = {'r.Streaming.PoolSize': '3000'}


def _failed_note(reason, project_id):
    return (f"This project's memory measurement failed: {reason}. An admin can retry it "
            f"(POST /api/projects/{project_id}/unreal-measurement/retry/).")


def _names(resp):
    assert resp.status_code == 200, resp.data
    return [job['name'] for job in resp.data]


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.fixture
def held(admin_client, asset, worker_with_token):
    """A normal job (held) and its pilot; the worker is ready."""
    worker, client = worker_with_token
    ready_worker(worker)
    job = created_job(admin_client, asset)
    return job, pilots()[0], worker, client


@pytest.mark.django_db
class TestHolding:

    def test_poll_returns_the_pilot_and_never_the_held_job(self, admin_client, held):
        job, pilot, _worker, client = held
        assert _names(client.get(JOBS, POLL)) == [pilot.name]
        assert _names(admin_client.get(JOBS, POLL)) == [pilot.name]

    def test_claim_of_the_held_job_is_refused(self, held):
        job, _pilot, worker, client = held
        resp = claim(client, job, worker)
        assert (resp.status_code, resp.data) == (409, {'error': NOT_TESTED})

    def test_routing_note_is_pending_and_none_for_the_pilot(self, admin_client, held):
        job, pilot, _worker, _client = held
        assert admin_client.get(f'{JOBS}{job.pk}/').data['routing_note'] == PENDING
        assert admin_client.get(f'{JOBS}{pilot.pk}/').data['routing_note'] is None

    def test_after_measurement_the_job_is_offered_with_its_result(self, admin_client, held):
        job, pilot, worker, client = held
        record = record_of(pilot)
        measure(record, peak_mb=8102)
        resp = client.get(JOBS, POLL)
        assert _names(resp) == [job.name]
        assert resp.data[0]['unreal_machine_results'] == [{
            'measurement_id': record.pk, 'card_kind': KIND, 'peak_mb': 8102, 'ram': None,
            'shared_peak_mb': None, 'driver_version': None,
        }]
        detail = admin_client.get(f'{JOBS}{job.pk}/').data
        assert (detail['unreal_machine_results'], detail['routing_note']) == (None, None)
        assert claim(client, job, worker).status_code == 200

    def test_pilot_is_claimable(self, held):
        _job, pilot, worker, client = held
        resp = claim(client, pilot, worker)
        assert resp.status_code == 200, resp.data
        assert resp.data['unreal_kind'] == 'pilot'
        assert resp.data['unreal_machine_results'] is None


@pytest.mark.django_db
class TestStates:

    def test_none_state(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        ready_worker(worker)
        job = Job.objects.create(name='PreMigration', asset=asset, output_file_pattern='//r/f.png',
                                 start_frame=1, end_frame=1, render_settings=dict(RENDER_SETTINGS))
        # FR-MT-U-2: an online machine that has not tried the key is waited for.
        assert admin_client.get(f'{JOBS}{job.pk}/').data['routing_note'] == PENDING
        assert claim(client, job, worker).data == {'error': NOT_TESTED}
        assert _names(client.get(JOBS, POLL)) == []

    @pytest.mark.parametrize('update, reason', [
        ({'status': 'ERROR', 'error_message': 'Pilot render failed: boom'}, 'Pilot render failed: boom'),
        ({'status': 'CANCELED'}, 'Pilot job was canceled.'),
        ({'status': 'DONE'}, 'Pilot job finished without a measurement.'),
    ])
    def test_failed_states(self, admin_client, held, update, reason):
        job, pilot, worker, client = held
        Job.objects.filter(pk=pilot.pk).update(**update)
        note = _failed_note(reason, job.asset.project_id)
        assert admin_client.get(f'{JOBS}{job.pk}/').data['routing_note'] == note
        assert claim(client, job, worker).data == {'error': note}

    def test_deleted_pilot(self, admin_client, held):
        job, pilot, _worker, _client = held
        pilot.delete()
        note = _failed_note('Pilot job was deleted.', job.asset.project_id)
        assert admin_client.get(f'{JOBS}{job.pk}/').data['routing_note'] == note

    def test_requeue_limit_note_carries_the_infrastructure_sentence(self, admin_client, held):
        job, pilot, _worker, _client = held
        Job.objects.filter(pk=pilot.pk).update(status='ERROR', error_message='Max auto-requeue attempts exceeded (3)')
        note = admin_client.get(f'{JOBS}{job.pk}/').data['routing_note']
        assert note == _failed_note('Max auto-requeue attempts exceeded (3)', job.asset.project_id) + (
            " The pilot was released or requeued too often; this points to a worker or network "
            "problem, not to the project.")

    def test_long_reason_is_truncated(self, admin_client, held):
        job, pilot, _worker, _client = held
        Job.objects.filter(pk=pilot.pk).update(status='ERROR', error_message='x' * 500)
        note = admin_client.get(f'{JOBS}{job.pk}/').data['routing_note']
        assert note == _failed_note('x' * 200, job.asset.project_id)


@pytest.mark.django_db
class TestPilotExclusions:

    def test_worker_without_a_card_reading_gets_no_pilot(self, held):
        _job, pilot, worker, client = held
        worker.gpu_memory = gpu_memory(32607, source='override')
        worker.save(update_fields=['gpu_memory'])
        assert _names(client.get(JOBS, POLL)) == []
        assert claim(client, pilot, worker).data == {
            'error': "Worker cannot measure GPU memory (no live card reading)."}

    def test_admin_poll_still_lists_the_pilot(self, admin_client, held):
        _job, pilot, _worker, _client = held
        assert _names(admin_client.get(JOBS, POLL)) == [pilot.name]

    def test_stale_pilot_and_its_return(self, admin_client, held):
        _job, old_pilot, worker, client = held
        project_url = f'/api/projects/{old_pilot.asset.project_id}/'
        admin_client.patch(project_url, {'unreal_cvar_overrides': CVARS}, format='json')
        new_pilot = pilots()[-1]
        assert _names(client.get(JOBS, POLL)) == [new_pilot.name]
        assert claim(client, old_pilot, worker).data == {
            'error': "Pilot is stale: the project's CVars changed since it was created."}
        old_pilot.refresh_from_db()
        assert old_pilot.status == 'QUEUED'
        admin_client.patch(project_url, {'unreal_cvar_overrides': {}}, format='json')
        assert old_pilot.name in _names(client.get(JOBS, POLL))
        assert claim(client, old_pilot, worker).status_code == 200

    def test_superseded_pilot(self, admin_client, held):
        """Per-machine D2: a later pilot of the same pair makes the earlier record not current."""
        job, pilot, worker, client = held
        record = record_of(pilot)
        url = f'/api/projects/{pilot.asset.project_id}/unreal-measurement/retry/'
        measure(record)
        resp = admin_client.post(url, {'job_id': job.pk, 'remeasure': True}, format='json')
        assert resp.status_code == 201, resp.data
        remeasure_pilot = Job.objects.get(pk=resp.data['pilot_job_id'])
        assert remeasure_pilot.name in _names(client.get(JOBS, POLL))
        record.refresh_from_db()
        assert (record.current, record_of(remeasure_pilot).current) == (False, True)
        with transaction.atomic():
            locked = lock_project(job.asset.project)
            create_pilot(locked, job.asset, Target.of(job), worker=worker, card_kind=KIND, reason='test')
        assert remeasure_pilot.name not in _names(client.get(JOBS, POLL))
        assert claim(client, remeasure_pilot, worker).data == {
            'error': "Pilot is no longer needed: its target was measured by a later pilot."}


@pytest.mark.django_db
class TestQueueOrder:

    def test_older_job_of_another_project_comes_before_the_pilot(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        ready_worker(worker)
        other = unreal_asset(unreal_project(name='OtherProj'), name='OtherAsset')
        old = created_job(admin_client, other, name='OtherOld')
        measure(record_of(pilots(other)[0]))
        Job.objects.filter(pk=old.pk).update(submitted_at=timezone.now() - timedelta(hours=1))
        created_job(admin_client, asset, name='Mine')
        names = _names(client.get(JOBS, POLL))
        assert names == ['OtherOld', pilots(asset)[0].name]


def _online_worker(hostname, total):
    worker = Worker.objects.create(hostname=hostname, is_active=True, unreal_capabilities=caps(),
                                   gpu_memory=gpu_memory(total))
    return ready_worker(worker, total_mb=total)


@pytest.mark.django_db
class TestNoFarmMemoryNote:
    """Per-machine D7, S2: no pilot minimum, no 15% margin, no pool-caps bound in the routing note."""

    def test_pilot_has_no_memory_need(self, admin_client):
        set_share(SHARE)
        project = unreal_project(unreal_cvar_overrides={
            'r.Streaming.PoolSize': '9000', 'r.RayTracing.ResidentGeometryMemoryPoolSizeInMB': '2048'})
        asset = unreal_asset(project)
        _online_worker('vram-12', 12282)
        created_job(admin_client, asset)
        pilot = pilots()[0]
        assert admin_client.get(f'{JOBS}{pilot.pk}/').data['routing_note'] is None

    def test_measured_job_has_no_farm_estimate(self, admin_client):
        set_share(SHARE)
        project = unreal_project(unreal_cvar_overrides={
            'r.Streaming.PoolSize': '9000', 'r.RayTracing.ResidentGeometryMemoryPoolSizeInMB': '2048'})
        asset = unreal_asset(project)
        _online_worker('vram-11', 11264)
        job = created_job(admin_client, asset)
        measure(record_of(pilots()[0]), peak_mb=7930)
        assert admin_client.get(f'{JOBS}{job.pk}/').data['routing_note'] is None


@pytest.mark.django_db
def test_claim_reads_the_results_a_fixed_number_of_times(held):
    """Per-machine FR-MT-T-2/3: the claim row, the caller's pair states, and the response's results."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    job, pilot, worker, client = held
    measure(record_of(pilot))
    with CaptureQueriesContext(connection) as ctx:
        resp = claim(client, job, worker)
    assert resp.status_code == 200 and resp.data['unreal_machine_results'][0]['peak_mb'] == 8102
    assert len([q for q in ctx.captured_queries if 'workers_unrealmeasurement' in q['sql']]) == 3
