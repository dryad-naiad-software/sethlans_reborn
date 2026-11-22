# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: what a pilot render ran with (gpu-memory-reader FR-GC-7..10,
FR-GT-12, AC-29, AC-31, AC-32).

* ``pilot-result/`` stores ``game_overrides`` and ``texture_streaming`` with
  the other results; any other value, or a missing field, is a 400 that
  stores nothing.
* Both stay out of the measurement key: index, key state, claim and poll.
* A write through the project endpoint changes neither.  (The project's
  single ``unreal_measurement.latest`` that showed them is gone:
  per-machine FR-MT-U-1 lists machines per key instead.)
"""

import json
from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from workers.models import Job, UnrealMeasurement
from workers.renderers import unreal_measured as um
from workers.renderers.unreal_machine import JOB_KEY_REFS, current_records, machine_poll_q
from workers.services.unreal_pilot import key_state_of

from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import (
    JOBS, POLL, claim, claimed_pilot, created_job, measure, pilot_body, pilots, post_result, ready_worker,
    record_of,
)

PROJECTS = '/api/projects/'
FIELDS = ('game_overrides', 'texture_streaming')
PAIRS = [('implicit', 'off'), ('preset', 'fully_load')]


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
def pilot(admin_client, asset, worker_with_token):
    worker, client = worker_with_token
    return claimed_pilot(admin_client, asset, worker, client), worker, client


def _pair(record):
    record.refresh_from_db()
    return record.game_overrides, record.texture_streaming


def _key_state(job):
    return key_state_of(job.asset.project, job.asset_id, job.asset.project.unreal_overrides_sha256,
                        job.unreal_target_sha256).state


def _machines(client, project_id):
    resp = client.get(f'{PROJECTS}{project_id}/')
    assert resp.status_code == 200, resp.data
    [key] = resp.data['unreal_measurement']['keys']
    return key['machines']


@pytest.mark.django_db
class TestPilotResult:

    @pytest.mark.parametrize('pair', PAIRS + [('none', 'unchanged'), ('unknown', 'unknown')])
    def test_valid_result_stores_both_values_and_the_rest(self, pilot, pair):
        job, worker, client = pilot
        body = pilot_body(game_overrides=pair[0], texture_streaming=pair[1])
        resp = post_result(client, job.pk, body)
        assert (resp.status_code, resp.data) == (200, {'status': 'DONE', 'peak_mb': 8102})
        record = record_of(job)
        assert _pair(record) == pair
        assert (record.peak_mb, record.peak_source, record.card_delta_peak_mb, record.frames) == (8102, 'csv', 7930, 23)
        assert (record.worker_id, record.gpu_total_mb, record.measured_at is not None) == (worker.pk, 32607, True)

    @pytest.mark.parametrize('field', FIELDS)
    @pytest.mark.parametrize('value', ['other', '', None, 5, 'OFF', 'missing'])
    def test_bad_or_missing_value_is_400_and_stores_nothing(self, pilot, field, value):
        job, _worker, client = pilot
        body = pilot_body()
        if value == 'missing':
            del body[field]
        else:
            body[field] = value
        resp = post_result(client, job.pk, body)
        assert resp.status_code == 400 and set(resp.data) == {field}, resp.data
        job.refresh_from_db()
        record = record_of(job)
        assert (job.status, record.peak_mb, _pair(record)) == ('RENDERING', None, ('', ''))

    def test_unknown_key_check_still_refuses_extra_keys(self, pilot):
        job, _worker, client = pilot
        resp = post_result(client, job.pk, pilot_body(streaming='off'))
        assert (resp.status_code, resp.data) == (400, {'detail': 'Unknown keys: streaming.'})


@pytest.mark.django_db
class TestKeyUnchanged:
    """FR-GC-9, AC-31: the two fields are never part of the measurement key."""

    def test_not_in_the_key_index(self):
        index = next(i for i in UnrealMeasurement._meta.indexes if i.name == 'unrealmeas_key_latest_idx')
        assert index.fields == ['asset', 'overrides_sha256', 'target_sha256', '-created_at', '-id']

    def test_not_read_by_the_key_lookup_claim_or_poll(self, admin_client, asset):
        job = created_job(admin_client, asset)
        # The key filter itself (a plain select would list every column).
        statements = [str(current_records(job.asset_id, 'a' * 64, 'b' * 64).values('pk').query),
                      str(Job.objects.alias(**um.key_aliases(JOB_KEY_REFS, '_k_')).values('pk').query),
                      str(Job.objects.filter(machine_poll_q(None)).query)]
        with CaptureQueriesContext(connection) as ctx:
            assert um.claim_row(job) is not None
        statements += [query['sql'] for query in ctx.captured_queries]
        for sql in statements:
            assert not any(field in sql for field in FIELDS), sql

    @pytest.mark.parametrize('older, newer', [(PAIRS[0], PAIRS[1]), (PAIRS[1], PAIRS[0])])
    def test_records_differing_only_in_them_share_one_key(self, admin_client, asset, machine, older, newer):
        job = created_job(admin_client, asset)
        first = record_of(pilots()[0])
        measure(first, peak_mb=8102, game_overrides=older[0], texture_streaming=older[1])
        second = UnrealMeasurement.objects.get(pk=first.pk)
        second.pk = None
        second.pilot_job = None
        second.created_at = first.created_at + timedelta(seconds=1)
        second.peak_mb, second.game_overrides, second.texture_streaming = 9000, newer[0], newer[1]
        # Per-machine D2: the later record is the pair's current one.
        UnrealMeasurement.objects.filter(pk=first.pk).update(current=False)
        second.save()
        assert _key_state(job) == um.MEASURED
        [result] = machine[1].get(f'{JOBS}{job.pk}/').data['unreal_machine_results']
        assert (result['measurement_id'], result['peak_mb']) == (second.pk, 9000)
        [row] = _machines(admin_client, asset.project_id)
        assert (row['state'], row['peak_mb']) == ('fits', 9000)

    @pytest.mark.parametrize('pair', PAIRS)
    def test_a_job_is_claimable_whatever_the_values(self, admin_client, asset, worker_with_token, pair):
        worker, client = worker_with_token
        ready_worker(worker)
        job = created_job(admin_client, asset)
        measure(record_of(pilots()[0]), game_overrides=pair[0], texture_streaming=pair[1])
        assert job.name in [row['name'] for row in client.get(JOBS, POLL).data]
        assert claim(client, job, worker).status_code == 200

    def test_a_result_with_other_values_creates_no_new_key(self, admin_client, asset, pilot):
        job, _worker, client = pilot
        assert post_result(client, job.pk, pilot_body(game_overrides='preset',
                                                      texture_streaming='unchanged')).status_code == 200
        keys = set(UnrealMeasurement.objects.values_list('asset_id', 'overrides_sha256', 'target_sha256'))
        assert UnrealMeasurement.objects.count() == 1 and len(keys) == 1
        second = created_job(admin_client, asset, name='Still02')
        assert len(pilots()) == 1 and UnrealMeasurement.objects.count() == 1
        assert _key_state(second) == um.MEASURED


@pytest.mark.django_db
class TestProjectEndpoint:

    def test_a_write_through_the_project_endpoint_changes_nothing(self, admin_client, pilot):
        job, _worker, client = pilot
        post_result(client, job.pk, pilot_body())
        project_id = job.asset.project_id
        resp = admin_client.patch(f'{PROJECTS}{project_id}/', {
            'game_overrides': 'preset', 'texture_streaming': 'fully_load',
            'unreal_measurement': {'latest': {'game_overrides': 'preset', 'texture_streaming': 'fully_load'}},
        }, format='json')
        assert resp.status_code == 200, resp.data
        assert 'latest' not in resp.data['unreal_measurement']
        assert _pair(record_of(job)) == ('implicit', 'off')


@pytest.mark.django_db
class TestOpenAPI:

    @staticmethod
    def _schema():
        resp = Client().get('/api/schema/?format=json', HTTP_ACCEPT='application/json')
        assert resp.status_code == 200
        return json.loads(resp.content.decode('utf-8'))

    def test_enums_are_pinned_and_stable(self):
        schema = self._schema()
        comps = schema['components']['schemas']
        assert comps['UnrealGameOverridesEnum']['enum'] == ['implicit', 'none', 'preset', 'unknown']
        assert comps['UnrealTextureStreamingEnum']['enum'] == ['fully_load', 'off', 'unchanged', 'unknown']
        assert not [name for name in comps if name != 'UnrealGameOverridesEnum' and 'GameOverrides' in name]
        assert not [name for name in comps if name != 'UnrealTextureStreamingEnum' and 'TextureStreaming' in name]
        assert self._schema() == schema

    def test_pilot_result_requires_both(self):
        request = self._schema()['components']['schemas']['PilotResultRequest']
        assert set(FIELDS) <= set(request['required'])
        assert request['properties']['game_overrides'] == {'$ref': '#/components/schemas/UnrealGameOverridesEnum'}
        assert request['properties']['texture_streaming'] == {
            '$ref': '#/components/schemas/UnrealTextureStreamingEnum'}
