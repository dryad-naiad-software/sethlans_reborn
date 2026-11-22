# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: where the job API shows ``unreal_machines`` (Unreal
per-machine memory test FR-MT-U-1, FR-MT-U-3, chunk 8) and the schema.

Covers: the render job detail (QUEUED and RENDERING) lists its key's
machines; the job list, the create and update responses, pilots and
Blender jobs show null; a worker-token request gets null on the detail
and in its list, and no response to a worker names another machine's
hostname or driver; OpenAPI: ``MachineRow`` with its fields, ``state`` as
the pinned ``MachineStateEnum`` that the memory report reuses (no second
enum of the same values), the three lists referencing ``MachineRow``,
``LatestMeasurement`` gone.
"""

import json

import pytest
from django.test import Client

from workers.models import Job

from .machine_rows_helpers import ROW_KEYS, detail, key_args, keyed_job, machine, machines, pairs
from .machine_test_helpers import KIND_AMD, kinds_worker, pair_record
from .unreal_helpers import RENDER_SETTINGS
from .unreal_pilot_helpers import (
    DRIVER, JOBS, KIND, OTHER_SEQUENCE, POLL, created_job, fits_record, pilots, settings,
)

pytestmark = pytest.mark.django_db
OTHER_HOST = 'host-other-secret'
OTHER_DRIVER = '99.0.12.3456'


@pytest.fixture
def job(db):
    return keyed_job()


@pytest.fixture
def two_machines(job, make_worker_client):
    """The token worker (fits) and another machine with a hostname and driver of its own."""
    worker, client, _token = make_worker_client('host-own')
    kinds_worker(worker, KIND)
    fits_record(*key_args(job), worker, driver_version=DRIVER)
    other = machine(OTHER_HOST)
    pair_record(*key_args(job), other, verdict='does_not_fit', lower_bound_mb=30000, driver_version=OTHER_DRIVER)
    return worker, client, other


class TestJobDetail:

    def test_render_job_detail_lists_its_machines(self, admin_client, job, two_machines):
        rows = machines(admin_client, job)
        assert all(set(row) == ROW_KEYS for row in rows)
        assert pairs(rows) == [('host-other-secret', KIND, 'does_not_fit', True), ('host-own', KIND, 'fits', True)]

    def test_a_rendering_job_still_lists_them(self, admin_client, job, two_machines):
        Job.objects.filter(pk=job.pk).update(status='RENDERING')
        assert len(machines(admin_client, job)) == 2

    def test_pilot_job_is_null(self, admin_client, job, two_machines):
        pilot = pair_record(*key_args(job), two_machines[2], kind=KIND_AMD, pilot='QUEUED').pilot_job
        assert detail(admin_client, pilot)['unreal_kind'] == 'pilot'
        assert machines(admin_client, pilot) is None

    def test_blender_job_is_null(self, admin_client, asset):
        blend = Job.objects.create(name='BlendRows', asset=asset, output_file_pattern='//r/b.png')
        assert machines(admin_client, blend) is None


class TestOtherResponses:

    def test_job_list_is_null(self, admin_client, job, two_machines):
        rows = admin_client.get(JOBS, {'renderer': 'UNREAL'}).data
        assert rows and all(row['unreal_machines'] is None for row in rows)

    def test_create_and_update_responses_are_null(self, admin_client, job, two_machines):
        resp = admin_client.post(JOBS, {
            'name': 'RowsNew', 'asset_id': job.asset_id, 'output_file_pattern': '//r/f.png',
            'start_frame': 1, 'end_frame': 1, 'render_settings': dict(RENDER_SETTINGS)}, format='json')
        assert resp.status_code == 201, resp.data
        assert (resp.data['unreal_kind'], resp.data['unreal_machines']) == ('render', None)
        resp = admin_client.patch(f'{JOBS}{resp.data["id"]}/', {'name': 'RowsRenamed'}, format='json')
        assert resp.status_code == 200, resp.data
        assert resp.data['unreal_machines'] is None

    def test_created_job_detail_shows_the_pilots_as_testing(self, admin_client, job, two_machines):
        created = created_job(admin_client, job.asset, name='RowsCreated',
                              render_settings=settings(sequence=OTHER_SEQUENCE))
        assert len(pilots(job.asset)) == 2
        assert [row['state'] for row in machines(admin_client, created)] == ['testing', 'testing']


class TestWorkerToken:

    def test_detail_is_null_for_a_worker(self, job, two_machines):
        _worker, client, _other = two_machines
        data = detail(client, job)
        assert (data['unreal_machines'], data['routing_note']) == (None, None)

    def test_no_worker_response_names_another_machine(self, job, two_machines):
        worker, client, _other = two_machines
        responses = [client.get(f'{JOBS}{job.pk}/'), client.get(JOBS, {'renderer': 'UNREAL'}),
                     client.get(JOBS, {**POLL, 'worker_id': worker.pk})]
        for resp in responses:
            assert resp.status_code == 200, resp.data
            text = json.dumps(resp.data, default=str)
            assert OTHER_HOST not in text and OTHER_DRIVER not in text
        listed = responses[1].data
        assert listed and all(row['unreal_machines'] is None for row in listed)
        # The worker's own result stays visible to it (FR-MT-T-3).
        [own] = responses[0].data['unreal_machine_results']
        assert own['driver_version'] == DRIVER


def _schemas():
    resp = Client().get('/api/schema/?format=json', HTTP_ACCEPT='application/json')
    assert resp.status_code == 200
    return json.loads(resp.content.decode('utf-8'))['components']['schemas']


def _items_ref(prop):
    """The ``$ref`` of a (nullable) array property's items."""
    return prop['items']['$ref']


class TestOpenAPI:

    def test_machine_row_schema(self):
        schemas = _schemas()
        row = schemas['MachineRow']
        assert set(row['properties']) == ROW_KEYS
        assert row['properties']['state'] == {'$ref': '#/components/schemas/MachineStateEnum'}
        assert schemas['MachineStateEnum']['enum'] == [
            'fits', 'does_not_fit', 'testing', 'failed', 'retest_due', 'not_tried']
        assert '#/components/schemas/UnrealRam' in json.dumps(row['properties']['ram'])
        for name in ('worker', 'card_name', 'card_total_mb', 'peak_mb', 'lower_bound_mb', 'driver_version',
                     'reason', 'ram'):
            assert row['properties'][name].get('nullable') is True, name

    def test_one_state_enum(self):
        """FR-MT-U-3: the memory report's pair state and the row's state are the same pinned enum."""
        schemas = _schemas()
        states = sorted(schemas['MachineStateEnum']['enum'])
        same = [name for name, schema in schemas.items() if sorted(schema.get('enum') or []) == states]
        assert same == ['MachineStateEnum']

    def test_the_lists_reference_machine_row(self):
        schemas = _schemas()
        ref = '#/components/schemas/MachineRow'
        job = schemas['Job']['properties']['unreal_machines']
        submission = schemas['SequenceSubmissionDetail']['properties']['machines']
        assert _items_ref(job) == _items_ref(submission) == ref
        assert job['nullable'] is True and submission['nullable'] is True
        assert _items_ref(schemas['MeasurementKey']['properties']['machines']) == ref
        keys = schemas['MeasurementSummary']['properties']['keys']
        assert _items_ref(keys) == '#/components/schemas/MeasurementKey' and keys['nullable'] is True

    def test_latest_measurement_is_gone(self):
        schemas = _schemas()
        assert 'LatestMeasurement' not in schemas
        assert 'latest' not in schemas['MeasurementSummary']['properties']
