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
* ``unreal_measurement.latest`` shows them read-only, ``null`` until
  measured and for records measured before migration ``0026``.
"""

import json
from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from workers.models import Job, UnrealMeasurement
from workers.renderers import unreal_measured as um

from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import (
    JOBS, POLL, claim, claimed_pilot, created_job, measure, pilot_body, pilots, post_result, ready_worker,
    record_of,
)

PROJECTS = '/api/projects/'
FIELDS = ('game_overrides', 'texture_streaming')
PAIRS = [('implicit', 'off'), ('preset', 'fully_load')]


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.fixture
def pilot(admin_client, asset, worker_with_token):
    worker, client = worker_with_token
    return claimed_pilot(admin_client, asset, worker, client), worker, client


def _pair(record):
    record.refresh_from_db()
    return record.game_overrides, record.texture_streaming


def _latest(client, project_id):
    resp = client.get(f'{PROJECTS}{project_id}/')
    assert resp.status_code == 200, resp.data
    return resp.data['unreal_measurement']['latest']


@pytest.mark.django_db
class TestPilotResult:

    @pytest.mark.parametrize('pair', PAIRS + [('none', 'unchanged'), ('unknown', 'unknown')])
    def test_valid_result_stores_both_values_and_the_rest(self, pilot, pair):
        job, worker, client = pilot
        body = pilot_body(game_overrides=pair[0], texture_streaming=pair[1])
        resp = post_result(client, job.pk, body)
        assert (resp.status_code, resp.data) == (200, {'status': 'DONE', 'peak_mb': 8102, 'gate_need_mb': 9318})
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
        statements = [str(um.key_records(job.asset_id, 'a' * 64, 'b' * 64).values('pk').query),
                      str(um.job_annotations(Job.objects.all()).query),
                      str(Job.objects.filter(um.poll_measured_q(True)).query)]
        with CaptureQueriesContext(connection) as ctx:
            assert um.claim_row(job) is not None
        statements += [query['sql'] for query in ctx.captured_queries]
        for sql in statements:
            assert not any(field in sql for field in FIELDS), sql

    @pytest.mark.parametrize('older, newer', [(PAIRS[0], PAIRS[1]), (PAIRS[1], PAIRS[0])])
    def test_records_differing_only_in_them_share_one_key(self, admin_client, asset, older, newer):
        job = created_job(admin_client, asset)
        first = record_of(pilots()[0])
        measure(first, peak_mb=8102, game_overrides=older[0], texture_streaming=older[1])
        second = UnrealMeasurement.objects.get(pk=first.pk)
        second.pk = None
        second.pilot_job = None
        second.created_at = first.created_at + timedelta(seconds=1)
        second.peak_mb, second.game_overrides, second.texture_streaming = 9000, newer[0], newer[1]
        second.save()
        row = um.claim_row(job)
        assert (row['_unreal_key_state'], row['_unreal_peak_mb']) == (um.MEASURED, 9000)
        assert admin_client.get(f'{JOBS}{job.pk}/').data['unreal_measured_peak_mb'] == 9000
        latest = _latest(admin_client, asset.project_id)
        assert (latest['measurement_id'], latest['game_overrides'], latest['texture_streaming']) == (
            second.pk, newer[0], newer[1])

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
        assert um.claim_row(second)['_unreal_key_state'] == um.MEASURED


@pytest.mark.django_db
class TestLatest:
    """FR-GC-10, AC-32: read-only, null until measured, never ''."""

    def test_null_before_measurement(self, admin_client, asset):
        created_job(admin_client, asset)
        latest = _latest(admin_client, asset.project_id)
        assert (latest['status'], latest['game_overrides'], latest['texture_streaming']) == ('pending', None, None)

    def test_values_after_the_pilot_result(self, admin_client, pilot):
        job, _worker, client = pilot
        assert post_result(client, job.pk, pilot_body()).status_code == 200
        latest = _latest(admin_client, job.asset.project_id)
        assert (latest['status'], latest['game_overrides'], latest['texture_streaming']) == (
            'measured', 'implicit', 'off')

    def test_a_record_measured_before_0026_shows_null_not_blank(self, admin_client, asset):
        created_job(admin_client, asset)
        measure(record_of(pilots()[0]), peak_mb=8102, game_overrides='', texture_streaming='')
        latest = _latest(admin_client, asset.project_id)
        assert (latest['status'], latest['peak_mb']) == ('measured', 8102)
        assert (latest['game_overrides'], latest['texture_streaming']) == (None, None)
        listed = admin_client.get(PROJECTS).data[0]['unreal_measurement']['latest']
        assert (listed['game_overrides'], listed['texture_streaming']) == (None, None)

    def test_a_failed_record_shows_null(self, admin_client, asset):
        created_job(admin_client, asset)
        Job.objects.filter(pk=pilots()[0].pk).update(status='ERROR', error_message='boom')
        latest = _latest(admin_client, asset.project_id)
        assert (latest['status'], latest['game_overrides'], latest['texture_streaming']) == ('failed', None, None)

    def test_a_write_through_the_project_endpoint_changes_nothing(self, admin_client, pilot):
        job, _worker, client = pilot
        post_result(client, job.pk, pilot_body())
        project_id = job.asset.project_id
        resp = admin_client.patch(f'{PROJECTS}{project_id}/', {
            'game_overrides': 'preset', 'texture_streaming': 'fully_load',
            'unreal_measurement': {'latest': {'game_overrides': 'preset', 'texture_streaming': 'fully_load'}},
        }, format='json')
        assert resp.status_code == 200, resp.data
        assert (resp.data['unreal_measurement']['latest']['game_overrides'],
                resp.data['unreal_measurement']['latest']['texture_streaming']) == ('implicit', 'off')
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

    def test_latest_fields_are_read_only_and_nullable(self):
        props = self._schema()['components']['schemas']['LatestMeasurement']['properties']
        for field, enum in zip(FIELDS, ('UnrealGameOverridesEnum', 'UnrealTextureStreamingEnum')):
            assert props[field]['readOnly'] is True and props[field]['nullable'] is True
            assert {'$ref': f'#/components/schemas/{enum}'} in props[field]['oneOf']
