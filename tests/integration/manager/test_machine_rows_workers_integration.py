# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: which machines get a row (Unreal per-machine memory
test D5, FR-MT-U-1, chunk 8).

Covers: an offline active worker that would pass D5 on its stored
heartbeat is listed ``not_tried`` with ``online`` false, and one that
would not (share not reachable, no live card reading, other build) is not
listed; an offline worker's record keeps its state; an online worker that
is not eligible gets no untried row; a record of a kind the worker no
longer ticks is still listed beside its untried row; a disabled worker's
record is listed ``online`` false under its stored hostname and a disabled
worker without one is not listed; deleting a worker ends its records'
currency (D4), so they leave the list; a current record without a worker
shows ``worker`` null and its stored hostname; without the share only the
records are listed.
"""

import pytest

from workers.models import UnrealMeasurement, Worker

from .machine_rows_helpers import STORED_NAME, key_args, keyed_job, machine, machines, pairs, row_of
from .machine_test_helpers import KIND_AMD, kind_card, pair_record, set_cards
from .unreal_helpers import caps, gpu_memory, set_share
from .unreal_pilot_helpers import KIND

pytestmark = pytest.mark.django_db


@pytest.fixture
def job(db):
    return keyed_job()


class TestOffline:

    def test_offline_eligible_pair_is_not_tried_and_off(self, admin_client, job):
        machine('host-on')
        off = machine('host-off', online=False)
        assert pairs(machines(admin_client, job)) == [
            ('host-off', KIND, 'not_tried', False), ('host-on', KIND, 'not_tried', True)]
        row = row_of(machines(admin_client, job), off)
        assert (row['card_name'], row['card_total_mb']) == (STORED_NAME, 32607)

    def test_offline_record_keeps_its_state(self, admin_client, job):
        off = machine('host-off', online=False)
        pair_record(*key_args(job), off, verdict='fits', peak=9100)
        row = row_of(machines(admin_client, job), off)
        assert (row['state'], row['peak_mb'], row['online']) == ('fits', 9100, False)

    @pytest.mark.parametrize('change', [
        {'unreal_capabilities': caps(reachable=False)},
        {'unreal_capabilities': caps(builds=('5.7.1-46543410',))},
        {'gpu_memory': gpu_memory(32607, source='override')},
    ], ids=['share-unreachable', 'other-build', 'no-live-reading'])
    def test_offline_worker_that_fails_d5_is_not_listed(self, admin_client, job, change):
        off = machine('host-off', online=False)
        Worker.objects.filter(pk=off.pk).update(**change)
        assert machines(admin_client, job) == []


class TestOnlineEligibility:

    def test_untick_removes_the_untried_row(self, admin_client, job):
        worker = machine('host-a')
        set_cards(worker, [kind_card(KIND, 0, ticked=False)])
        assert machines(admin_client, job) == []

    def test_worker_without_the_build_is_not_listed(self, admin_client, job):
        worker = machine('host-a')
        Worker.objects.filter(pk=worker.pk).update(unreal_capabilities=caps(builds=('5.7.1-46543410',)))
        assert machines(admin_client, job) == []

    def test_record_of_a_kind_no_longer_ticked_stays_beside_the_untried_row(self, admin_client, job):
        worker = machine('host-a', KIND_AMD)
        pair_record(*key_args(job), worker, kind=KIND, verdict='fits')
        assert pairs(machines(admin_client, job)) == [
            ('host-a', KIND_AMD, 'not_tried', True), ('host-a', KIND, 'fits', True)]

    def test_two_kinds_are_two_rows(self, admin_client, job):
        worker = machine('host-a', KIND, KIND_AMD)
        pair_record(*key_args(job), worker, kind=KIND_AMD, pilot='QUEUED')
        assert pairs(machines(admin_client, job)) == [
            ('host-a', KIND_AMD, 'testing', True), ('host-a', KIND, 'not_tried', True)]


class TestDisabledAndDeleted:

    def test_disabled_worker_record_is_listed_off_under_its_stored_hostname(self, admin_client, job):
        worker = machine('host-a', active=False)
        record = pair_record(*key_args(job), worker, verdict='fits')
        UnrealMeasurement.objects.filter(pk=record.pk).update(worker_hostname='host-a-stored')
        assert machines(admin_client, job) == [{
            'worker': worker.pk, 'hostname': 'host-a-stored', 'online': False, 'card_kind': KIND,
            'card_name': None, 'card_total_mb': None, 'state': 'fits', 'peak_mb': 8102, 'lower_bound_mb': None,
            'driver_version': None, 'reason': None, 'covered': False, 'ram': None,
        }]

    def test_disabled_worker_without_a_record_is_not_listed(self, admin_client, job):
        machine('host-a', active=False)
        machine('host-b', active=False, online=False)
        assert machines(admin_client, job) == []

    def test_deleting_a_worker_takes_its_records_off_the_list(self, admin_client, job):
        gone = machine('host-gone')
        record = pair_record(*key_args(job), gone, pilot='QUEUED')
        stay = machine('host-stay')
        Worker.objects.get(pk=gone.pk).delete()
        record.refresh_from_db()
        assert (record.worker_id, record.current) == (None, False)
        assert pairs(machines(admin_client, job)) == [('host-stay', KIND, 'not_tried', True)]
        assert machines(admin_client, job)[0]['worker'] == stay.pk

    def test_current_record_without_a_worker_shows_null_and_the_stored_hostname(self, admin_client, job):
        gone = machine('host-gone')
        record = pair_record(*key_args(job), gone, verdict='fits', gpu_name='Example GPU 24', gpu_total_mb=24564)
        Worker.objects.filter(pk=gone.pk).update(is_active=False)
        UnrealMeasurement.objects.filter(pk=record.pk).update(worker=None)
        [row] = machines(admin_client, job)
        assert (row['worker'], row['hostname'], row['online'], row['state']) == (None, 'host-gone', False, 'fits')
        assert (row['card_name'], row['card_total_mb']) == ('Example GPU 24', 24564)


class TestNoShare:

    def test_without_the_share_only_records_are_listed(self, admin_client, job):
        worker = machine('host-a')
        machine('host-b')
        pair_record(*key_args(job), worker, verdict='fits')
        set_share('')
        assert pairs(machines(admin_client, job)) == [('host-a', KIND, 'fits', True)]
