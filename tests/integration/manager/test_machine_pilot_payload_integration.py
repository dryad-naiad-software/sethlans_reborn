# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: what a stored pilot result gives the render jobs of its
key, the pool-CVar project rule and the schema (Unreal per-machine memory
test FR-MT-V-3, D7, D11, FR-MT-U-3, AC-10).

Covers: a pilot result reaches the normal job's ``unreal_machine_results``
with its own record id, peak, RAM, shared peak and driver (a blank driver
as null); with two card kinds on one machine each entry carries the RAM
of exactly the record that gives its peak (AC-10), in the poll, the
detail and the claim; ``check_pool_cvars`` keeps the removed pool-caps
gate's messages for a negative or non-integer pool size and accepts plain
sizes in any letter case; the OpenAPI schema pins
``MeasurementVerdictEnum`` on the request and drops ``gate_need_mb``.
"""

import json

import pytest
from django.test import Client

from workers.models import Job

from .machine_test_helpers import KIND_AMD, kinds_worker, render_job
from .machine_verdict_helpers import NEW_DRIVER, poll_ids
from .unreal_helpers import unreal_asset, unreal_project
from .unreal_pilot_helpers import (
    JOBS, KIND, PILOT_RAM, POLL, claim, claimed_pilot, fits_record, pilot_body, post_result, record_of,
)

PROJECTS = '/api/projects/'
POOL = 'r.Streaming.PoolSize'
RT_POOL = 'r.RayTracing.ResidentGeometryMemoryPoolSizeInMB'
RAM_NV = {'prep': {'ws_mb': 4000, 'commit_mb': 8000}, 'warmup': None, 'render': {'ws_mb': 6000, 'commit_mb': 19000}}
RAM_AMD = {'prep': {'ws_mb': 4100, 'commit_mb': 9100},
           'warmup': {'ws_mb': 9000, 'commit_mb': 26000, 'cold': True},
           'render': {'ws_mb': 6100, 'commit_mb': 22100}}


@pytest.fixture
def asset(db):
    return unreal_asset()


def _results(client, job):
    resp = client.get(f'{JOBS}{job.pk}/')
    assert resp.status_code == 200, resp.data
    return resp.data['unreal_machine_results']


@pytest.mark.django_db
class TestPilotResultReachesTheJob:

    def test_the_entry_is_the_pilots_record(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        pilot = claimed_pilot(admin_client, asset, worker, client)
        body = pilot_body(driver_version=NEW_DRIVER, shared_peak_mb=412)
        assert post_result(client, pilot.pk, body).status_code == 200
        normal = Job.objects.get(name='Still01')
        assert _results(client, normal) == [{
            'measurement_id': record_of(pilot).pk, 'card_kind': KIND, 'peak_mb': 8102, 'ram': PILOT_RAM,
            'shared_peak_mb': 412, 'driver_version': NEW_DRIVER}]
        assert normal.pk in poll_ids(client)

    @pytest.mark.parametrize('driver', [None, 'Unknown'])
    def test_an_unknown_driver_is_sent_as_null(self, admin_client, asset, worker_with_token, driver):
        worker, client = worker_with_token
        pilot = claimed_pilot(admin_client, asset, worker, client)
        assert post_result(client, pilot.pk, pilot_body(driver_version=driver)).status_code == 200
        [entry] = _results(client, Job.objects.get(name='Still01'))
        assert entry['driver_version'] is None

    def test_a_does_not_fit_pilot_sends_nothing(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        pilot = claimed_pilot(admin_client, asset, worker, client)
        assert post_result(client, pilot.pk, pilot_body(verdict='does_not_fit', lower_bound_mb=8102)).status_code == 200
        normal = Job.objects.get(name='Still01')
        assert _results(client, normal) == []
        assert normal.pk not in poll_ids(client)


@pytest.mark.django_db
class TestRamOfTheRecordGivingThePeak:
    """AC-10: one machine with two card kinds; each kind's entry is its own record's peak and RAM."""

    @pytest.fixture
    def two_kinds(self, asset, worker_with_token):
        worker, client = worker_with_token
        kinds_worker(worker, KIND, KIND_AMD)
        job = render_job(asset, name='MtRamTwoKinds')
        nv = fits_record(asset, job.unreal_target_sha256, worker, peak_mb=8000, ram=RAM_NV, shared_peak_mb=0,
                         driver_version='32.0.16.1692')
        amd = fits_record(asset, job.unreal_target_sha256, worker, peak_mb=9000, kind=KIND_AMD, ram=RAM_AMD,
                          job_peak_max_mb=9500, driver_version='32.0.21042.62')
        return job, worker, client, nv, amd

    @staticmethod
    def _expected(nv, amd):
        return [
            {'measurement_id': amd.pk, 'card_kind': KIND_AMD, 'peak_mb': 9500, 'ram': RAM_AMD,
             'shared_peak_mb': None, 'driver_version': '32.0.21042.62'},
            {'measurement_id': nv.pk, 'card_kind': KIND, 'peak_mb': 8000, 'ram': RAM_NV,
             'shared_peak_mb': 0, 'driver_version': '32.0.16.1692'},
        ]

    def test_detail_and_poll(self, two_kinds):
        job, _worker, client, nv, amd = two_kinds
        assert _results(client, job) == self._expected(nv, amd)
        listed = {row['id']: row for row in client.get(JOBS, POLL).data}
        assert listed[job.pk]['unreal_machine_results'] == self._expected(nv, amd)

    def test_claim(self, two_kinds):
        job, worker, client, nv, amd = two_kinds
        resp = claim(client, job, worker)
        assert resp.status_code == 200, resp.data
        assert resp.data['unreal_machine_results'] == self._expected(nv, amd)


def _patch_cvars(admin_client, project, cvars):
    return admin_client.patch(f'{PROJECTS}{project.pk}/', {'unreal_cvar_overrides': cvars}, format='json')


@pytest.mark.django_db
class TestPoolCvars:

    @pytest.mark.parametrize('cvars, message', [
        ({POOL: '-1'}, f"{POOL}: pool size must not be negative"),
        ({RT_POOL: -512}, f"{RT_POOL}: pool size must not be negative"),
        ({POOL: 'abc'}, f"{POOL}: pool size 'abc' is not an integer number of MB"),
        ({POOL: '1.5'}, f"{POOL}: pool size '1.5' is not an integer number of MB"),
        ({'r.streaming.poolsize': '4e3'}, f"{POOL}: pool size '4e3' is not an integer number of MB"),
    ], ids=['negative', 'negative-int', 'word', 'fraction', 'other-case'])
    def test_refusals_keep_todays_messages(self, admin_client, cvars, message):
        project = unreal_project(name='MtPool')
        resp = _patch_cvars(admin_client, project, cvars)
        assert (resp.status_code, resp.data) == (400, {'unreal_cvar_overrides': [message]})
        project.refresh_from_db()
        assert project.unreal_cvar_overrides == {}

    @pytest.mark.parametrize('cvars, stored', [
        ({POOL: '0', RT_POOL: 2048}, {POOL: '0', RT_POOL: '2048'}),
        ({'R.STREAMING.POOLSIZE': '9000'}, {'R.STREAMING.POOLSIZE': '9000'}),
        ({'r.Other.Value': '-1'}, {'r.Other.Value': '-1'}),
    ], ids=['plain-sizes', 'other-case', 'not-a-pool'])
    def test_plain_sizes_are_accepted(self, admin_client, cvars, stored):
        project = unreal_project(name='MtPoolOk')
        resp = _patch_cvars(admin_client, project, cvars)
        assert resp.status_code == 200, resp.data
        project.refresh_from_db()
        assert project.unreal_cvar_overrides == stored


@pytest.mark.django_db
class TestOpenAPI:

    @staticmethod
    def _schemas():
        resp = Client().get('/api/schema/?format=json', HTTP_ACCEPT='application/json')
        assert resp.status_code == 200
        return json.loads(resp.content.decode('utf-8'))['components']['schemas']

    def test_the_verdict_enum_is_pinned(self):
        schemas = self._schemas()
        assert schemas['MeasurementVerdictEnum']['enum'] == ['fits', 'does_not_fit']
        request = schemas['PilotResultRequest']
        assert request['properties']['verdict'] == {'$ref': '#/components/schemas/MeasurementVerdictEnum'}
        new = {'card_kind', 'driver_version', 'verdict', 'shared_peak_mb', 'lower_bound_mb'}
        assert new <= set(request['required'])
        driver = request['properties']['driver_version']
        assert (driver['nullable'], driver['maxLength']) == (True, 40)

    def test_gate_need_is_gone(self):
        schemas = self._schemas()
        assert set(schemas['PilotResultResponse']['properties']) == {'status', 'peak_mb'}
        assert 'gate_need_mb' not in schemas['LatestMeasurement']['properties']
