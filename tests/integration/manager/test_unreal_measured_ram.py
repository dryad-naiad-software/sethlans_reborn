# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: a key's RAM figures (per-card spec FR-PC-MGR-2, FR-PC-MGR-4).

* ``key_ram`` reads exactly the record ``key_peak`` reads (the latest
  successful one), never a later or earlier record's figures.
* ``KeyState`` carries ``ram``; ``key_state``, ``key_state_of``,
  ``cached_key_state``, ``claim_row`` and ``measured_claim_rejection``
  fill or cache it.
* The job field ``unreal_measured_ram``, the blender-invariance key list,
  ``unreal_measurement.latest`` and the OpenAPI schema.
"""

import dataclasses
import json
from datetime import timedelta

import pytest
from django.core.files.base import ContentFile
from django.test import Client
from django.utils import timezone

from shared.unreal.validation import overrides_sha256

from workers.models import Asset, Job, UnrealMeasurement
from workers.renderers import unreal_measured as um
from workers.serializers.unreal_measurement import LatestMeasurementSerializer
from workers.services.unreal_pilot import KeyState, cached_key_state, key_state, key_state_of

from .blender_invariance_normalize import NEW_KEYS_BY_KIND
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import (
    JOBS, PILOT_RAM, claim, claimed_pilot, created_job, pilot_body, pilots, post_result, ready_worker,
)

PROJECTS = '/api/projects/'
RAM_A = {'prep': {'ws_mb': 4000, 'commit_mb': 8000}, 'warmup': None, 'render': {'ws_mb': 6000, 'commit_mb': 19000}}
RAM_B = {'prep': {'ws_mb': 4100, 'commit_mb': 8100},
         'warmup': {'ws_mb': 9000, 'commit_mb': 23000, 'cold': False},
         'render': {'ws_mb': 6100, 'commit_mb': 19100}}


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.fixture
def job(admin_client, asset):
    """A normal job (its key has one pending pilot record)."""
    return created_job(admin_client, asset)


def _copy(job, *, peak=None, ram=None, age=0, created_at=None, process=None, shared=None):
    """Another record of ``job``'s key (no pilot job), created ``age`` minutes ago."""
    job.refresh_from_db()
    project = job.asset.project
    return UnrealMeasurement.objects.create(
        project=project, asset=job.asset, overrides_sha256=project.unreal_overrides_sha256,
        target_sha256=job.unreal_target_sha256, engine_build=job.engine_build,
        map='/Game/M.M', sequence='/Game/S.S', preset='/Game/P.P', peak_mb=peak,
        peak_source='card' if peak else '', ram=ram, ram_process_commit_peak_mb=process, shared_machine=shared,
        created_at=created_at or timezone.now() - timedelta(minutes=age),
        measured_at=timezone.now() if peak else None,
    )


def _annotated(job):
    return um.job_annotations(Job.objects.filter(pk=job.pk)).values('_unreal_peak_mb', '_unreal_ram').get()


@pytest.mark.django_db
class TestKeyRamIsThePeakRecord:

    def test_the_latest_successful_record_gives_both(self, job):
        _copy(job, peak=7000, ram=RAM_A, age=10)
        _copy(job, peak=8000, ram=RAM_B, age=5)
        assert _annotated(job) == {'_unreal_peak_mb': 8000, '_unreal_ram': RAM_B}

    def test_a_later_record_without_ram_gives_null_not_the_older_figures(self, job):
        _copy(job, peak=7000, ram=RAM_A, age=10)
        _copy(job, peak=8000, ram=None, age=5)
        assert _annotated(job) == {'_unreal_peak_mb': 8000, '_unreal_ram': None}

    def test_a_later_unmeasured_record_with_figures_is_ignored(self, job):
        _copy(job, peak=8000, ram=RAM_A, age=10)
        _copy(job, peak=None, ram=RAM_B, age=1)  # pending: no peak, so not the peak's record
        assert _annotated(job) == {'_unreal_peak_mb': 8000, '_unreal_ram': RAM_A}

    def test_a_created_at_tie_is_broken_by_id_for_both(self, job):
        at = timezone.now() - timedelta(minutes=3)
        _copy(job, peak=7000, ram=RAM_A, created_at=at)
        _copy(job, peak=8000, ram=RAM_B, created_at=at)
        assert _annotated(job) == {'_unreal_peak_mb': 8000, '_unreal_ram': RAM_B}

    def test_an_unmeasured_key_reads_null(self, job):
        assert _annotated(job) == {'_unreal_peak_mb': None, '_unreal_ram': None}


@pytest.mark.django_db
class TestKeyState:

    def test_key_state_gains_ram_as_its_fourth_field(self):
        assert [field.name for field in dataclasses.fields(KeyState)] == ['state', 'reason', 'peak_mb', 'ram']

    def test_key_state_and_key_state_of_carry_the_peak_record_s_ram(self, job):
        _copy(job, peak=7000, ram=RAM_B, age=10)
        _copy(job, peak=8000, ram=RAM_A, age=5)
        expected = KeyState('measured', None, 8000, RAM_A)
        assert key_state(job) == expected
        project = job.asset.project
        assert key_state_of(project, job.asset_id, overrides_sha256({}), job.unreal_target_sha256) == expected

    def test_blender_jobs_and_pilots_read_four_nones(self, job):
        assert key_state(pilots()[0]) == KeyState(None, None, None, None)

    def test_cached_key_state_stores_ram_and_reads_it_without_a_query(self, job, django_assert_num_queries):
        _copy(job, peak=8000, ram=RAM_A)
        fresh = Job.objects.get(pk=job.pk)
        with django_assert_num_queries(1):
            assert cached_key_state(fresh).ram == RAM_A
        assert fresh._unreal_ram == RAM_A
        with django_assert_num_queries(0):
            again = cached_key_state(fresh)
        assert (again.state, again.peak_mb, again.ram) == ('measured', 8000, RAM_A)


@pytest.mark.django_db
class TestClaimCache:

    def test_claim_row_reads_ram(self, job):
        _copy(job, peak=8000, ram=RAM_B)
        row = um.claim_row(job)
        assert (row['_unreal_peak_mb'], row['_unreal_ram']) == (8000, RAM_B)

    def test_measured_claim_rejection_caches_ram_beside_the_peak(
            self, job, worker_with_token, django_assert_num_queries):
        worker, _client = worker_with_token
        ready_worker(worker)
        _copy(job, peak=8000, ram=RAM_B)
        fresh = Job.objects.get(pk=job.pk)
        assert um.measured_claim_rejection(worker, fresh) is None
        assert (fresh._unreal_peak_mb, fresh._unreal_ram) == (8000, RAM_B)
        with django_assert_num_queries(0):
            assert cached_key_state(fresh).ram == RAM_B

    def test_the_claim_response_carries_the_figures(self, job, worker_with_token):
        worker, client = worker_with_token
        ready_worker(worker)
        _copy(job, peak=8000, ram=RAM_A)
        resp = claim(client, job, worker)
        assert resp.status_code == 200, resp.data
        assert (resp.data['unreal_measured_peak_mb'], resp.data['unreal_measured_ram']) == (8000, RAM_A)


@pytest.mark.django_db
class TestJobField:

    def test_list_and_detail_carry_the_figures(self, admin_client, job):
        _copy(job, peak=8000, ram=RAM_B)
        listed = {row['name']: row for row in admin_client.get(JOBS).data}
        assert listed[job.name]['unreal_measured_ram'] == RAM_B
        assert admin_client.get(f'{JOBS}{job.pk}/').data['unreal_measured_ram'] == RAM_B

    def test_null_while_unmeasured_and_for_a_peak_record_without_ram(self, admin_client, job):
        assert admin_client.get(f'{JOBS}{job.pk}/').data['unreal_measured_ram'] is None
        _copy(job, peak=8000, ram=None)
        detail = admin_client.get(f'{JOBS}{job.pk}/').data
        assert (detail['unreal_measured_peak_mb'], detail['unreal_measured_ram']) == (8000, None)

    def test_null_for_pilots_even_when_their_key_is_measured(self, admin_client, job):
        _copy(job, peak=8000, ram=RAM_A)
        assert admin_client.get(f'{JOBS}{pilots()[0].pk}/').data['unreal_measured_ram'] is None

    def test_null_for_blender_jobs(self, admin_client, project):
        blend = Asset.objects.create(name='BlendRamAsset', project=project,
                                     blend_file=ContentFile(b'BLENDER' + b'\x00' * 57, name='r.blend'))
        blender_job = Job.objects.create(name='BlendRam', asset=blend, output_file_pattern='//r/b.png')
        assert admin_client.get(f'{JOBS}{blender_job.pk}/').data['unreal_measured_ram'] is None

    def test_a_pilot_result_reaches_the_normal_job(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        pilot = claimed_pilot(admin_client, asset, worker, client)
        assert post_result(client, pilot.pk, pilot_body()).status_code == 200
        normal = Job.objects.get(name='Still01')
        assert admin_client.get(f'{JOBS}{normal.pk}/').data['unreal_measured_ram'] == PILOT_RAM

    def test_the_blender_invariance_list_has_the_new_key(self):
        assert 'unreal_measured_ram' in NEW_KEYS_BY_KIND['job']


def _latest(client, project_id):
    resp = client.get(f'{PROJECTS}{project_id}/')
    assert resp.status_code == 200, resp.data
    return resp.data['unreal_measurement']['latest']


@pytest.mark.django_db
class TestLatest:

    def test_the_serializer_declares_the_fields_read_only_and_nullable(self):
        fields = LatestMeasurementSerializer().fields
        for name in ('ram', 'ram_process_commit_peak_mb', 'shared_machine'):
            assert fields[name].read_only and fields[name].allow_null

    def test_null_before_measurement(self, admin_client, job):
        latest = _latest(admin_client, job.asset.project_id)
        assert latest['status'] == 'pending'
        assert (latest['ram'], latest['ram_process_commit_peak_mb'], latest['shared_machine']) == (None, None, None)

    def test_after_the_pilot_result(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        pilot = claimed_pilot(admin_client, asset, worker, client)
        assert post_result(client, pilot.pk, pilot_body(shared_machine=True)).status_code == 200
        latest = _latest(admin_client, asset.project_id)
        assert (latest['ram'], latest['ram_process_commit_peak_mb'], latest['shared_machine']) == (
            PILOT_RAM, 18900, True)
        listed = admin_client.get(PROJECTS).data[0]['unreal_measurement']['latest']
        assert (listed['ram'], listed['shared_machine']) == (PILOT_RAM, True)

    def test_a_failed_record_hides_any_stored_figures(self, admin_client, job):
        UnrealMeasurement.objects.filter(pilot_job=pilots()[0]).update(
            ram=RAM_A, ram_process_commit_peak_mb=100, shared_machine=True)
        Job.objects.filter(pk=pilots()[0].pk).update(status='ERROR', error_message='boom')
        latest = _latest(admin_client, job.asset.project_id)
        assert (latest['status'], latest['ram'], latest['ram_process_commit_peak_mb'], latest['shared_machine']) == (
            'failed', None, None, None)


@pytest.mark.django_db
class TestOpenAPI:

    @staticmethod
    def _schemas():
        resp = Client().get('/api/schema/?format=json', HTTP_ACCEPT='application/json')
        assert resp.status_code == 200
        return json.loads(resp.content.decode('utf-8'))['components']['schemas']

    def test_the_job_field_is_a_typed_nullable_object(self):
        schemas = self._schemas()
        field = schemas['Job']['properties']['unreal_measured_ram']
        assert field['readOnly'] is True and field['nullable'] is True
        assert {'$ref': '#/components/schemas/UnrealRam'} in field['allOf'] + field.get('oneOf', [])
        ram = schemas['UnrealRam']
        assert set(ram['required']) == {'prep', 'warmup', 'render'}
        assert set(schemas['UnrealRamWarmup']['required']) == {'ws_mb', 'commit_mb', 'cold'}
        assert schemas['UnrealRamPhase']['properties']['ws_mb']['minimum'] == 1

    def test_the_request_and_latest_use_the_same_object(self):
        schemas = self._schemas()
        request = schemas['PilotResultRequest']
        assert {'ram', 'ram_process_commit_peak_mb', 'shared_machine'} <= set(request['required'])
        assert request['properties']['ram_process_commit_peak_mb']['minimum'] == 1
        latest = schemas['LatestMeasurement']['properties']
        assert latest['ram']['readOnly'] is True and latest['ram']['nullable'] is True
