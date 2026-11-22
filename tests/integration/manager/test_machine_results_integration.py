# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: per-machine result records, worker deletion, the
``unreal_machine_results`` field and ``unreal_memory_report`` clearing
(Unreal per-machine memory test D2, D4, FR-MT-R-1, FR-MT-R-2, FR-MT-R-5,
FR-MT-T-3).

Covers: two creations for one pair leave exactly one current record (the
newer) and leave other pairs alone; a ``retest_due`` pair gets a new
pilot and the old record stops being current; deleting a worker cancels
its QUEUED and RENDERING targeted pilots (logging how many), keeps
finished ones and every record, none of them current any more (a worker
with only a fits result cancels nothing); ``unreal_machine_results`` gives each worker only its own fits
records of its eligible kinds, null for admins and for pilots, built
with one query per response for list and detail; the accepted memory
report is cleared on release, admin requeue and reconciliation.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from workers import signals
from workers.models import Job, JobStatus, UnrealMeasurement, Worker

from .machine_test_helpers import (
    KIND_AMD, TARGET, TARGET_DELETED, current_of, kinds_worker, locked_create, locked_ensure, pair_record,
    render_job,
)
from .unreal_helpers import unreal_asset, unreal_project
from .unreal_pilot_helpers import JOBS, KIND, POLL, claim, created_job, pilots, ready_worker, record_of

REPORT = {'job_peak_mb': 9000}


@pytest.fixture
def uasset(db):
    return unreal_asset()


@pytest.fixture
def two(worker_with_token, make_worker_client):
    worker_a, client_a = worker_with_token
    worker_b, client_b, _token = make_worker_client('mt-res-b')
    ready_worker(worker_a)
    ready_worker(worker_b)
    return worker_a, client_a, worker_b, client_b


@pytest.mark.django_db
class TestOneCurrentPerPair:

    def test_two_creations_leave_the_newer_current(self, uasset, two):
        worker_a, _client_a, worker_b, _client_b = two
        key = TARGET.sha256()
        other = locked_create(uasset, TARGET, worker_b)
        first = locked_create(uasset, TARGET, worker_a)
        second = locked_create(uasset, TARGET, worker_a)
        assert current_of(uasset, key, worker_a) == [second.pk]
        assert current_of(uasset, key, worker_b) == [other.pk]
        assert UnrealMeasurement.objects.get(pk=first.pk).current is False
        assert (second.worker_id, second.card_kind, second.pilot_job.target_worker_id) == (worker_a.pk, KIND, worker_a.pk)
        assert second.pilot_job.render_settings['unreal.card_kind'] == KIND
        assert second.base_target_sha256 == TARGET.base_sha256()

    def test_other_kind_of_the_same_worker_is_its_own_pair(self, uasset, worker_with_token):
        worker, _client = worker_with_token
        kinds_worker(worker, KIND, KIND_AMD)
        nvidia = locked_create(uasset, TARGET, worker, KIND)
        locked_create(uasset, TARGET, worker, KIND_AMD)
        amd = locked_create(uasset, TARGET, worker, KIND_AMD)
        assert current_of(uasset, TARGET.sha256(), worker, KIND) == [nvidia.pk]
        assert current_of(uasset, TARGET.sha256(), worker, KIND_AMD) == [amd.pk]

    def test_ensure_skips_a_testing_pair_and_retests_a_retest_due_one(self, uasset, worker_with_token):
        worker, _client = worker_with_token
        ready_worker(worker)
        [pilot] = locked_ensure(uasset)
        assert locked_ensure(uasset) == []
        old = record_of(pilot)
        UnrealMeasurement.objects.filter(pk=old.pk).update(verdict='fits', peak_mb=8000, stale_reason='driver_changed')
        [again] = locked_ensure(uasset)
        assert current_of(uasset, TARGET.sha256(), worker) == [record_of(again).pk]
        assert UnrealMeasurement.objects.get(pk=old.pk).current is False

    @pytest.mark.parametrize('fields', [{'verdict': 'fits'}, {'verdict': 'does_not_fit'},
                                        {'pilot': 'ERROR', 'error': 'boom'}])
    def test_ensure_leaves_fits_does_not_fit_and_failed_pairs(self, uasset, worker_with_token, fields):
        worker, _client = worker_with_token
        ready_worker(worker)
        pair_record(uasset, TARGET.sha256(), worker, **fields)
        assert locked_ensure(uasset) == []


@pytest.mark.django_db
class TestWorkerDeletion:

    def test_open_pilots_canceled_records_kept(self, admin_client, uasset, two):
        worker_a, client_a, worker_b, _client_b = two
        done = pair_record(uasset, 'e' * 64, worker_a, verdict='fits', pilot='DONE')
        created_job(admin_client, uasset)
        created_job(admin_client, uasset, name='Other', render_settings={
            'unreal.map': '/Game/Maps/Main.Main', 'unreal.sequence': '/Game/Cinematics/Two.Two',
            'unreal.preset': '/Game/Presets/Cine.Cine'})
        mine = [p for p in pilots(uasset) if p.target_worker_id == worker_a.pk and p.status == 'QUEUED']
        theirs = [p for p in pilots(uasset) if p.target_worker_id == worker_b.pk]
        assert len(mine) == 2 and len(theirs) == 2
        assert claim(client_a, mine[0], worker_a).status_code == 200
        records = UnrealMeasurement.objects.count()
        Worker.objects.get(pk=worker_a.pk).delete()
        for pilot in mine:
            pilot.refresh_from_db()
            assert (pilot.status, pilot.error_message, pilot.target_worker_id) == ('CANCELED', TARGET_DELETED, None)
            assert pilot.completed_at is not None
        done.pilot_job.refresh_from_db()
        assert (done.pilot_job.status, done.pilot_job.target_worker_id) == ('DONE', None)
        assert UnrealMeasurement.objects.count() == records
        # Kept as history: the worker is gone (NULL) and none of its records is current any more.
        orphans = UnrealMeasurement.objects.filter(worker__isnull=True)
        assert (orphans.count(), orphans.filter(current=True).count()) == (3, 0)
        assert UnrealMeasurement.objects.filter(worker=worker_b, current=True).count() == 2
        assert {p.status for p in Job.objects.filter(pk__in=[p.pk for p in theirs])} == {'QUEUED'}

    def test_a_fits_only_worker_cancels_nothing_and_the_count_is_logged(self, uasset, two, mocker):
        worker_a, _client_a, worker_b, _client_b = two
        fits = pair_record(uasset, 'f' * 64, worker_b, verdict='fits', pilot='DONE')
        queued = pair_record(uasset, 'e' * 64, worker_a, pilot='QUEUED')
        rendering = pair_record(uasset, 'd' * 64, worker_a, pilot='RENDERING')
        info = mocker.patch.object(signals.logger, 'info')
        Worker.objects.get(pk=worker_b.pk).delete()
        info.assert_not_called()
        fits.refresh_from_db()
        fits.pilot_job.refresh_from_db()
        assert (fits.worker_id, fits.current, fits.verdict, fits.pilot_job.status) == (None, False, 'fits', 'DONE')
        Worker.objects.get(pk=worker_a.pk).delete()
        [call] = info.call_args_list
        assert call.args[1:] == (2, worker_a.hostname)
        for record in (queued, rendering):
            record.refresh_from_db()
            record.pilot_job.refresh_from_db()
            assert (record.current, record.pilot_job.status) == (False, 'CANCELED')


def _results_queries(ctx):
    """Statements whose outermost FROM is the measurement table (the results load)."""
    found = []
    for query in ctx.captured_queries:
        head = query['sql'].split(' FROM ', 1)
        if len(head) == 2 and head[1].startswith('"workers_unrealmeasurement"'):
            found.append(query['sql'])
    return found


@pytest.mark.django_db
class TestMachineResults:

    def test_each_worker_sees_only_its_own_results(self, admin_client, uasset, two):
        worker_a, client_a, worker_b, client_b = two
        job = render_job(uasset)
        mine = pair_record(uasset, job.unreal_target_sha256, worker_a, verdict='fits', peak=8000,
                           job_peak_max_mb=8500, driver_version='32.0.101.6881', shared_peak_mb=300)
        pair_record(uasset, job.unreal_target_sha256, worker_b, verdict='fits', peak=9100)
        pair_record(uasset, job.unreal_target_sha256, worker_a, KIND_AMD, verdict='fits', peak=7000)
        [result] = client_a.get(f'{JOBS}{job.pk}/').data['unreal_machine_results']
        assert result == {'measurement_id': mine.pk, 'card_kind': KIND, 'peak_mb': 8500, 'ram': None,
                          'shared_peak_mb': 300, 'driver_version': '32.0.101.6881'}
        assert [r['peak_mb'] for r in client_b.get(f'{JOBS}{job.pk}/').data['unreal_machine_results']] == [9100]
        assert admin_client.get(f'{JOBS}{job.pk}/').data['unreal_machine_results'] is None

    def test_empty_without_a_fits_result_and_null_for_a_pilot(self, admin_client, uasset, two):
        worker_a, client_a, _worker_b, _client_b = two
        job = created_job(admin_client, uasset)
        assert client_a.get(f'{JOBS}{job.pk}/').data['unreal_machine_results'] == []
        pilot = next(p for p in pilots(uasset) if p.target_worker_id == worker_a.pk)
        assert client_a.get(f'{JOBS}{pilot.pk}/').data['unreal_machine_results'] is None

    def test_one_query_per_list_response(self, uasset, worker_with_token):
        worker, client = worker_with_token
        ready_worker(worker)
        second_asset = unreal_asset(unreal_project(name='MtSecondProject'), name='MtSecondAsset')
        # Two keys per asset (presets differ), two assets: four keys, each with its own result.
        jobs = [render_job(asset, name=f'MtL{n}', render_settings={
                    'unreal.map': '/Game/Maps/Main.Main', 'unreal.sequence': '/Game/Cinematics/Edit.Edit',
                    'unreal.preset': f'/Game/Presets/P{n}.P{n}'})
                for n, asset in enumerate((uasset, uasset, second_asset, second_asset))]
        for job in jobs:
            pair_record(job.asset, job.unreal_target_sha256, worker, verdict='fits', peak=8000 + job.pk)
        with CaptureQueriesContext(connection) as ctx:
            resp = client.get(JOBS, POLL)
        assert resp.status_code == 200, resp.data
        assert {row['id']: row['unreal_machine_results'][0]['peak_mb'] for row in resp.data} == {
            job.pk: 8000 + job.pk for job in jobs}
        assert len(_results_queries(ctx)) == 1

    def test_one_query_per_detail_response(self, uasset, worker_with_token):
        worker, client = worker_with_token
        ready_worker(worker)
        job = render_job(uasset)
        pair_record(uasset, job.unreal_target_sha256, worker, verdict='fits')
        with CaptureQueriesContext(connection) as ctx:
            resp = client.get(f'{JOBS}{job.pk}/')
        assert resp.data['unreal_machine_results'][0]['peak_mb'] == 8102
        assert len(_results_queries(ctx)) == 1


def _rendering_with_report(asset, worker):
    job = render_job(asset)
    pair_record(asset, job.unreal_target_sha256, worker, verdict='fits')
    Job.objects.filter(pk=job.pk).update(status=JobStatus.RENDERING, assigned_worker=worker,
                                         unreal_memory_report=dict(REPORT))
    return job


@pytest.mark.django_db
class TestMemoryReportCleared:

    def test_on_release(self, uasset, worker_with_token):
        worker, client = worker_with_token
        ready_worker(worker)
        job = _rendering_with_report(uasset, worker)
        resp = client.post(f'{JOBS}{job.pk}/release/', {'reason': 'shutdown'}, format='json')
        assert resp.status_code == 200, resp.data
        job.refresh_from_db()
        assert (job.status, job.unreal_memory_report) == ('QUEUED', None)

    def test_on_admin_requeue(self, admin_client, uasset, worker_with_token):
        worker, _client = worker_with_token
        job = _rendering_with_report(uasset, worker)
        Job.objects.filter(pk=job.pk).update(status=JobStatus.ERROR)
        resp = admin_client.post(f'{JOBS}{job.pk}/requeue/')
        assert resp.status_code == 200, resp.data
        job.refresh_from_db()
        assert (job.status, job.unreal_memory_report) == ('QUEUED', None)

    def test_on_boot_reconciliation(self, uasset, worker_with_token):
        worker, client = worker_with_token
        ready_worker(worker)
        Worker.objects.filter(pk=worker.pk).update(boot_id='a' * 32)
        job = _rendering_with_report(uasset, worker)
        resp = client.post('/api/heartbeat/', {'hostname': worker.hostname, 'os': 'Windows',
                                               'worker_boot_id': 'b' * 32}, format='json')
        assert resp.status_code == 200, resp.data
        job.refresh_from_db()
        assert (job.status, job.unreal_memory_report) == ('QUEUED', None)
