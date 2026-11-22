# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: one machine row per D2 state (Unreal per-machine memory
test FR-MT-U-1, chunk 8), read from ``GET /api/jobs/{id}/``'s
``unreal_machines``.

Covers the row's exact field set; ``fits`` with the result peak
``max(peak, job_peak_max)``, driver, card and RAM from the record;
``does_not_fit`` with its lower bound; ``testing`` (QUEUED and RENDERING
pilot) with nulls and the stored card's name and size; ``failed`` with the
reason of each failure (capped at 200 characters); ``retest_due``;
``not_tried``; ``covered``; records that are not current, of other CVars
or of another key are not used; the sort by hostname (any case), card kind
and worker.
"""

import pytest

from workers.models import UnrealMeasurement

from .fits_nowhere_helpers import dnf_record
from .machine_rows_helpers import (
    ROW_KEYS, STORED_NAME, STORED_TOTAL, key_args, keyed_job, machine, machines, pairs, row_of,
)
from .machine_test_helpers import KIND_AMD, pair_record
from .unreal_pilot_helpers import DRIVER, KIND, PILOT_RAM

pytestmark = pytest.mark.django_db


@pytest.fixture
def job(db):
    return keyed_job()


@pytest.fixture
def worker(db):
    return machine('host-a')


def _row(client, job, worker, kind=KIND):
    return row_of(machines(client, job), worker, kind)


class TestMeasuredStates:

    def test_fits_row_carries_the_record(self, admin_client, job, worker):
        pair_record(*key_args(job), worker, verdict='fits', peak=8102, job_peak_max_mb=9000, ram=PILOT_RAM,
                    gpu_name='Example GPU 32', gpu_total_mb=32000, driver_version=DRIVER)
        assert _row(admin_client, job, worker) == {
            'worker': worker.pk, 'hostname': 'host-a', 'online': True, 'card_kind': KIND,
            'card_name': 'Example GPU 32', 'card_total_mb': 32000, 'state': 'fits', 'peak_mb': 9000,
            'lower_bound_mb': None, 'driver_version': DRIVER, 'reason': None, 'covered': False, 'ram': PILOT_RAM,
        }

    @pytest.mark.parametrize('job_peak', [None, 7000])
    def test_peak_is_the_pilot_peak_when_no_job_peaked_higher(self, admin_client, job, worker, job_peak):
        pair_record(*key_args(job), worker, verdict='fits', peak=8102, job_peak_max_mb=job_peak)
        row = _row(admin_client, job, worker)
        assert (row['peak_mb'], row['ram']) == (8102, None)

    def test_does_not_fit_row(self, admin_client, job, worker):
        dnf_record(*key_args(job), worker, peak=23100, driver_version=DRIVER)
        row = _row(admin_client, job, worker)
        assert set(row) == ROW_KEYS
        assert (row['state'], row['peak_mb'], row['lower_bound_mb']) == ('does_not_fit', 23100, 23100)
        assert (row['card_name'], row['card_total_mb'], row['reason']) == ('Example GPU 24', 24564, None)

    def test_retest_due_row_keeps_its_figures(self, admin_client, job, worker):
        pair_record(*key_args(job), worker, verdict='fits', peak=8102, stale='driver_changed', driver_version='1.0')
        row = _row(admin_client, job, worker)
        assert (row['state'], row['peak_mb'], row['driver_version'], row['reason']) == (
            'retest_due', 8102, '1.0', None)

    def test_covered_row(self, admin_client, job, worker):
        record = pair_record(*key_args(job), worker, verdict='fits', peak=8102)
        UnrealMeasurement.objects.filter(pk=record.pk).update(verdict_source='covered')
        row = _row(admin_client, job, worker)
        assert (row['state'], row['covered']) == ('fits', True)

    def test_blank_driver_is_null(self, admin_client, job, worker):
        pair_record(*key_args(job), worker, verdict='fits', driver_version='')
        assert _row(admin_client, job, worker)['driver_version'] is None


class TestOpenStates:

    @pytest.mark.parametrize('status', ['QUEUED', 'RENDERING'])
    def test_testing_row(self, admin_client, job, worker, status):
        # RAM stored before a result is never shown (null until measured).
        pair_record(*key_args(job), worker, pilot=status, ram=PILOT_RAM)
        assert _row(admin_client, job, worker) == {
            'worker': worker.pk, 'hostname': 'host-a', 'online': True, 'card_kind': KIND,
            'card_name': STORED_NAME, 'card_total_mb': STORED_TOTAL, 'state': 'testing', 'peak_mb': None,
            'lower_bound_mb': None, 'driver_version': None, 'reason': None, 'covered': False, 'ram': None,
        }

    @pytest.mark.parametrize(('pilot', 'error', 'reason'), [
        ('ERROR', 'boom', 'boom'),
        ('ERROR', 'x' * 300, 'x' * 200),
        ('CANCELED', '', "Pilot job was canceled."),
        ('DONE', '', "Pilot job finished without a measurement."),
        (None, '', "Pilot job was deleted."),
    ], ids=['error', 'capped', 'canceled', 'done', 'deleted'])
    def test_failed_row_and_its_reason(self, admin_client, job, worker, pilot, error, reason):
        pair_record(*key_args(job), worker, pilot=pilot, error=error)
        row = _row(admin_client, job, worker)
        assert (row['state'], row['reason'], row['peak_mb'], row['ram']) == ('failed', reason, None, None)

    def test_not_tried_row(self, admin_client, job, worker):
        assert machines(admin_client, job) == [{
            'worker': worker.pk, 'hostname': 'host-a', 'online': True, 'card_kind': KIND,
            'card_name': STORED_NAME, 'card_total_mb': STORED_TOTAL, 'state': 'not_tried', 'peak_mb': None,
            'lower_bound_mb': None, 'driver_version': None, 'reason': None, 'covered': False, 'ram': None,
        }]

    def test_second_kind_without_a_reading_total_has_a_null_size(self, admin_client, job):
        worker = machine('host-two', KIND, KIND_AMD)
        row = _row(admin_client, job, worker, KIND_AMD)
        assert (row['state'], row['card_name'], row['card_total_mb']) == ('not_tried', STORED_NAME, None)
        worker = machine('host-three', KIND, KIND_AMD, totals=(16000, 24000))
        assert _row(admin_client, job, worker, KIND_AMD)['card_total_mb'] == 24000


class TestWhichRecords:

    def test_a_record_that_is_not_current_is_not_used(self, admin_client, job, worker):
        pair_record(*key_args(job), worker, verdict='fits', current=False)
        assert pairs(machines(admin_client, job)) == [('host-a', KIND, 'not_tried', True)]

    def test_a_record_of_other_cvars_is_not_used(self, admin_client, job, worker):
        pair_record(*key_args(job), worker, verdict='fits', overrides='f' * 64)
        assert pairs(machines(admin_client, job)) == [('host-a', KIND, 'not_tried', True)]

    def test_a_record_of_another_key_is_not_used(self, admin_client, job, worker):
        pair_record(job.asset, 'e' * 64, worker, verdict='fits')
        other = keyed_job(job.asset, name='RowsOther', render_settings={**job.render_settings, 'unreal.sequence':
                                                                        '/Game/Cinematics/Other.Other'})
        pair_record(*key_args(other), worker, verdict='does_not_fit')
        assert pairs(machines(admin_client, job)) == [('host-a', KIND, 'not_tried', True)]
        assert pairs(machines(admin_client, other)) == [('host-a', KIND, 'does_not_fit', True)]

    def test_one_row_per_pair_with_every_state(self, admin_client, job):
        hosts = [machine(f'host-{n}') for n in range(5)]
        pair_record(*key_args(job), hosts[0], verdict='fits')
        dnf_record(*key_args(job), hosts[1])
        pair_record(*key_args(job), hosts[2], pilot='QUEUED')
        pair_record(*key_args(job), hosts[3], pilot='ERROR', error='boom')
        pair_record(*key_args(job), hosts[4], verdict='fits', stale='driver_changed')
        late = machine('host-5')
        assert [row['state'] for row in machines(admin_client, job)] == [
            'fits', 'does_not_fit', 'testing', 'failed', 'retest_due', 'not_tried']
        assert row_of(machines(admin_client, job), late)['state'] == 'not_tried'


class TestOrder:

    def test_sorted_by_hostname_any_case_then_kind_then_worker(self, admin_client, job):
        machine('host-c')
        machine('Host-B', KIND, KIND_AMD)
        first = machine('host-a')
        # A record whose worker is gone keeps its hostname; it sorts before the live one by worker.
        record = pair_record(*key_args(job), first, verdict='fits')
        UnrealMeasurement.objects.filter(pk=record.pk).update(worker=None)
        rows = machines(admin_client, job)
        assert [(row['hostname'], row['card_kind'], row['worker']) for row in rows] == [
            ('host-a', KIND, None), ('host-a', KIND, first.pk),
            ('Host-B', KIND_AMD, rows[2]['worker']), ('Host-B', KIND, rows[2]['worker']),
            ('host-c', KIND, rows[4]['worker']),
        ]
