# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: a key's RAM figures (per-card spec FR-PC-MGR-2, FR-PC-MGR-4;
per-machine memory test D2, D11, FR-MT-T-3, AC-10).

* A job's ``unreal_machine_results`` carry, per card kind, the RAM figures
  of exactly the record that gives the peak: the caller's own current,
  not stale, ``fits`` record of the key, never another record's figures.
* The field is null for other callers and for pilots and Blender jobs;
  the blender-invariance key list, the figures stored on the record from
  the pilot result and the OpenAPI schema.  (The project's single
  ``unreal_measurement.latest`` is gone: per-machine FR-MT-U-1 lists
  machines per key instead.)
"""

import json

import pytest
from django.core.files.base import ContentFile
from django.test import Client

from workers.models import Asset, Job, UnrealMeasurement

from .blender_invariance_normalize import NEW_KEYS_BY_KIND
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import (
    JOBS, KIND, PILOT_RAM, claim, claimed_pilot, created_job, fits_record, pilot_body, pilots, post_result,
    ready_worker,
)

PROJECTS = '/api/projects/'
RAM_A = {'prep': {'ws_mb': 4000, 'commit_mb': 8000}, 'warmup': None, 'render': {'ws_mb': 6000, 'commit_mb': 19000}}
RAM_B = {'prep': {'ws_mb': 4100, 'commit_mb': 8100},
         'warmup': {'ws_mb': 9000, 'commit_mb': 23000, 'cold': False},
         'render': {'ws_mb': 6100, 'commit_mb': 19100}}


@pytest.fixture
def machine(worker_with_token):
    """One eligible machine with one card kind (per-machine D5): ``(worker, client)``."""
    worker, client = worker_with_token
    ready_worker(worker)
    return worker, client


@pytest.fixture
def asset(db, machine):
    return unreal_asset()


@pytest.fixture
def job(admin_client, asset):
    """A normal job (its key has one testing pilot record for the machine)."""
    return created_job(admin_client, asset)


def _result(job, worker, **fields):
    """The machine's own current fits record of ``job``'s key (no pilot job)."""
    job.refresh_from_db()
    return fits_record(job.asset, job.unreal_target_sha256, worker, **fields)


def _results(client, job):
    resp = client.get(f'{JOBS}{job.pk}/')
    assert resp.status_code == 200, resp.data
    return resp.data['unreal_machine_results']


def _entry(record, peak_mb, ram):
    return {'measurement_id': record.pk, 'card_kind': KIND, 'peak_mb': peak_mb, 'ram': ram,
            'shared_peak_mb': None, 'driver_version': None}


@pytest.mark.django_db
class TestResultsAreTheCallersRecord:

    def test_the_current_fits_record_gives_peak_and_ram(self, job, machine):
        worker, client = machine
        record = _result(job, worker, peak_mb=8000, ram=RAM_B)
        assert _results(client, job) == [_entry(record, 8000, RAM_B)]

    def test_a_record_without_ram_gives_null(self, job, machine):
        worker, client = machine
        record = _result(job, worker, peak_mb=8000, ram=None)
        assert _results(client, job) == [_entry(record, 8000, None)]

    def test_a_record_that_is_no_longer_current_is_not_sent(self, job, machine):
        worker, client = machine
        older = _result(job, worker, peak_mb=7000, ram=RAM_A)
        newer = _result(job, worker, peak_mb=8000, ram=RAM_B)
        assert UnrealMeasurement.objects.get(pk=older.pk).current is False
        assert _results(client, job) == [_entry(newer, 8000, RAM_B)]

    def test_the_job_peak_raises_the_result_peak_and_keeps_the_ram(self, job, machine):
        worker, client = machine
        record = _result(job, worker, peak_mb=8000, ram=RAM_A, job_peak_max_mb=9100)
        assert _results(client, job) == [_entry(record, 9100, RAM_A)]

    @pytest.mark.parametrize('fields', [
        {'verdict': 'does_not_fit'}, {'verdict': ''}, {'stale_reason': 'driver_changed'},
    ], ids=['does_not_fit', 'no_verdict', 'stale'])
    def test_a_record_that_does_not_fit_is_not_sent(self, job, machine, fields):
        worker, client = machine
        _result(job, worker, peak_mb=8000, ram=RAM_A, **fields)
        assert _results(client, job) == []

    def test_another_machine_s_record_is_not_sent(self, job, machine, make_worker_client):
        _worker, client = machine
        other, _other_client, _token = make_worker_client('ram-other')
        _result(job, other, peak_mb=8000, ram=RAM_A)
        assert _results(client, job) == []


@pytest.mark.django_db
class TestClaim:

    def test_the_claim_response_carries_the_figures(self, job, machine):
        worker, client = machine
        record = _result(job, worker, peak_mb=8000, ram=RAM_A)
        resp = claim(client, job, worker)
        assert resp.status_code == 200, resp.data
        assert resp.data['unreal_machine_results'] == [_entry(record, 8000, RAM_A)]


@pytest.mark.django_db
class TestJobField:

    def test_null_for_admin_requests(self, admin_client, job, machine):
        _result(job, machine[0], peak_mb=8000, ram=RAM_B)
        listed = {row['name']: row for row in admin_client.get(JOBS).data}
        assert listed[job.name]['unreal_machine_results'] is None
        assert admin_client.get(f'{JOBS}{job.pk}/').data['unreal_machine_results'] is None

    def test_null_for_pilots_even_when_their_key_is_measured(self, job, machine):
        worker, client = machine
        _result(job, worker, peak_mb=8000, ram=RAM_A)
        assert _results(client, pilots()[0]) is None

    def test_null_for_blender_jobs(self, project, machine):
        blend = Asset.objects.create(name='BlendRamAsset', project=project,
                                     blend_file=ContentFile(b'BLENDER' + b'\x00' * 57, name='r.blend'))
        blender_job = Job.objects.create(name='BlendRam', asset=blend, output_file_pattern='//r/b.png')
        assert _results(machine[1], blender_job) is None

    def test_a_pilot_result_reaches_the_normal_job(self, admin_client, asset, machine):
        worker, client = machine
        pilot = claimed_pilot(admin_client, asset, worker, client)
        assert post_result(client, pilot.pk, pilot_body()).status_code == 200
        normal = Job.objects.get(name='Still01')
        [entry] = _results(client, normal)
        assert (entry['peak_mb'], entry['ram']) == (8102, PILOT_RAM)

    def test_the_blender_invariance_list_has_the_new_key(self):
        assert 'unreal_machine_results' in NEW_KEYS_BY_KIND['job']
        assert not {'unreal_measured_peak_mb', 'unreal_measured_ram'} & NEW_KEYS_BY_KIND['job']


def _figures(pilot):
    record = UnrealMeasurement.objects.get(pilot_job=pilot)
    return record.ram, record.ram_process_commit_peak_mb, record.shared_machine


@pytest.mark.django_db
class TestStoredFigures:

    def test_null_before_measurement(self, job):
        assert _figures(pilots()[0]) == (None, None, None)

    def test_after_the_pilot_result(self, admin_client, asset, machine):
        worker, client = machine
        pilot = claimed_pilot(admin_client, asset, worker, client)
        assert post_result(client, pilot.pk, pilot_body(shared_machine=True)).status_code == 200
        assert _figures(pilot) == (PILOT_RAM, 18900, True)


@pytest.mark.django_db
class TestOpenAPI:

    @staticmethod
    def _schemas():
        resp = Client().get('/api/schema/?format=json', HTTP_ACCEPT='application/json')
        assert resp.status_code == 200
        return json.loads(resp.content.decode('utf-8'))['components']['schemas']

    def test_the_job_field_is_a_typed_nullable_list(self):
        schemas = self._schemas()
        field = schemas['Job']['properties']['unreal_machine_results']
        assert field['readOnly'] is True and field['nullable'] is True and field['type'] == 'array'
        entry = schemas[field['items']['$ref'].rsplit('/', 1)[-1]]
        assert set(entry['required']) == {
            'measurement_id', 'card_kind', 'peak_mb', 'ram', 'shared_peak_mb', 'driver_version'}
        ram_ref = {'$ref': '#/components/schemas/UnrealRam'}
        assert ram_ref in entry['properties']['ram'].get('allOf', []) + entry['properties']['ram'].get('oneOf', [])
        ram = schemas['UnrealRam']
        assert set(ram['required']) == {'prep', 'warmup', 'render'}
        assert set(schemas['UnrealRamWarmup']['required']) == {'ws_mb', 'commit_mb', 'cold'}
        assert schemas['UnrealRamPhase']['properties']['ws_mb']['minimum'] == 1

    def test_the_request_requires_the_figures(self):
        schemas = self._schemas()
        request = schemas['PilotResultRequest']
        assert {'ram', 'ram_process_commit_peak_mb', 'shared_machine'} <= set(request['required'])
        assert request['properties']['ram_process_commit_peak_mb']['minimum'] == 1
        assert 'LatestMeasurement' not in schemas
