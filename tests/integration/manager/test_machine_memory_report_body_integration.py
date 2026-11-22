# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the strict ``memory-report/`` body, its access and its
schema (Unreal per-machine memory test FR-MT-S-3, FR-MT-U-3).

Covers: all eight keys are required (``null`` where allowed), an unknown
key is refused; ``spill_cause`` is set exactly when ``spilled`` is true;
the ``card_key`` form, the ``card_kind`` form, the ``driver_version``
pattern (UE's literal ``Unknown`` counts as null) and every number's
bounds and type; the body is checked before the job id (a bad body for an
unknown job is 400, a good one 404); only worker tokens may report.  Every
refusal leaves the job without a stored report and the record unchanged.
The OpenAPI schema pins ``SpillCauseEnum`` and answers with D2's ``MachineStateEnum``.
"""

import json

import pytest
from django.test import Client
from rest_framework.test import APIClient

from shared.unreal.vram_gate import MAX_PEAK_MB

from .memory_report_helpers import (
    BODY_KEYS, OLD_DRIVER, accepted, post_report, rendering_render, report_body, state, stored_report,
)
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import ready_worker


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.fixture
def rendering(asset, worker_with_token):
    worker, client = worker_with_token
    ready_worker(worker)
    job, record = rendering_render(asset, worker, client)
    return job, record, client


def _refused(rendering, body, field=None, code=400):
    job, record, client = rendering
    before = state(record)
    resp = post_report(client, job, body)
    assert resp.status_code == code, resp.data
    if field is not None:
        assert field in resp.data, resp.data
    assert stored_report(job) is None
    assert state(record) == before
    return resp


@pytest.mark.django_db
class TestRequiredKeys:

    @pytest.mark.parametrize('field', BODY_KEYS)
    def test_a_missing_key_is_400(self, rendering, field):
        body = report_body(rendering[1])
        del body[field]
        _refused(rendering, body, field)

    def test_an_unknown_key_is_400(self, rendering):
        resp = _refused(rendering, report_body(rendering[1], card_name='x'))
        assert resp.data == {'detail': 'Unknown keys: card_name.'}

    def test_a_list_body_is_400(self, rendering):
        _refused(rendering, [report_body(rendering[1])])

    def test_the_full_body_is_accepted(self, rendering):
        job, record, client = rendering
        accepted(client, job, report_body(record))


SPILL_PAIRS_REFUSED = [(True, None), (False, 'self'), (False, 'contention'), (None, 'self'), (None, 'contention')]
SPILL_PAIRS_OK = [(True, 'self'), (True, 'contention'), (False, None), (None, None)]


@pytest.mark.django_db
class TestSpillCause:

    @pytest.mark.parametrize('spilled,cause', SPILL_PAIRS_REFUSED, ids=[repr(p) for p in SPILL_PAIRS_REFUSED])
    def test_cause_set_exactly_when_spilled(self, rendering, spilled, cause):
        resp = _refused(rendering, report_body(rendering[1], spilled=spilled, spill_cause=cause), 'spill_cause')
        assert resp.data['spill_cause'] == ["Must be set exactly when spilled is true."]

    @pytest.mark.parametrize('spilled,cause', SPILL_PAIRS_OK, ids=[repr(p) for p in SPILL_PAIRS_OK])
    def test_consistent_pairs_are_accepted(self, rendering, spilled, cause):
        job, record, client = rendering
        assert post_report(client, job, report_body(record, spilled=spilled, spill_cause=cause)).status_code == 200

    @pytest.mark.parametrize('cause', ['SELF', 'other', '', 1, True])
    def test_unknown_cause_is_400(self, rendering, cause):
        _refused(rendering, report_body(rendering[1], spilled=True, spill_cause=cause), 'spill_cause')

    @pytest.mark.parametrize('spilled', ['true', 1, 0, 'false'])
    def test_spilled_is_a_strict_boolean(self, rendering, spilled):
        _refused(rendering, report_body(rendering[1], spilled=spilled), 'spilled')


BAD_CARD_KEYS = ['apple-gpu', 'pci:0000:01:00', 'PCI:0000:01:00.0', 'pci:0000:01:00.8', 'pci:0000:0G:00.0',
                 'pci:000:01:00.0', 'pci:000000000:01:00.0', 'pci:0000:01:00.0\n', '', 5, None]
BAD_KINDS = ['10DE:2B85:00A1', '10de:2b85', '10de:2b85:00a1:00', '10de:2b85:NONE', '', 5, None]
BAD_DRIVERS = ['1234567890.ABCDEFGHIJ-klmnopqrst.0987654x', '', ' 32.0', '32.0 ', '32 0', '32_0',
               'internal:32.0', 'Unknown ', 5, 32.0, True]


@pytest.mark.django_db
class TestFieldForms:

    @pytest.mark.parametrize('value', BAD_CARD_KEYS, ids=[repr(v) for v in BAD_CARD_KEYS])
    def test_card_key_form(self, rendering, value):
        _refused(rendering, report_body(rendering[1], card_key=value), 'card_key')

    @pytest.mark.parametrize('value', BAD_KINDS, ids=[repr(v) for v in BAD_KINDS])
    def test_card_kind_form(self, rendering, value):
        _refused(rendering, report_body(rendering[1], card_kind=value), 'card_kind')

    @pytest.mark.parametrize('value', BAD_DRIVERS, ids=[f'bad{i}' for i in range(len(BAD_DRIVERS))])
    def test_driver_pattern(self, rendering, value):
        _refused(rendering, report_body(rendering[1], driver_version=value), 'driver_version')

    @pytest.mark.parametrize('value', ['Unknown', None], ids=['unknown', 'null'])
    def test_unknown_driver_is_stored_as_null(self, rendering, value):
        job, record, client = rendering
        accepted(client, job, report_body(record, driver_version=value))
        assert stored_report(job)['report']['driver_version'] is None
        assert state(record)[2] == ''


BAD_IDS = [0, -1, 2 ** 63, True, '5', 5.0, None]
BAD_JOB_PEAKS = [0, -1, MAX_PEAK_MB + 1, True, '4000', 4000.0]
BAD_SHARED = [-1, MAX_PEAK_MB + 1, True, '96', 96.0]


@pytest.mark.django_db
class TestNumbers:

    @pytest.mark.parametrize('value', BAD_IDS, ids=[repr(v) for v in BAD_IDS])
    def test_measurement_id(self, rendering, value):
        _refused(rendering, report_body(rendering[1], measurement_id=value), 'measurement_id')

    @pytest.mark.parametrize('value', BAD_JOB_PEAKS, ids=[repr(v) for v in BAD_JOB_PEAKS])
    def test_job_peak_bounds(self, rendering, value):
        _refused(rendering, report_body(rendering[1], job_peak_mb=value), 'job_peak_mb')

    @pytest.mark.parametrize('value', BAD_SHARED, ids=[repr(v) for v in BAD_SHARED])
    def test_shared_peak_bounds(self, rendering, value):
        _refused(rendering, report_body(rendering[1], shared_peak_mb=value), 'shared_peak_mb')

    @pytest.mark.parametrize('field,value', [('job_peak_mb', 1), ('job_peak_mb', None), ('shared_peak_mb', 0),
                                             ('shared_peak_mb', MAX_PEAK_MB), ('shared_peak_mb', None)])
    def test_edges_are_accepted(self, rendering, field, value):
        job, record, client = rendering
        accepted(client, job, report_body(record, **{field: value}))
        assert stored_report(job)['report'][field] == value


@pytest.mark.django_db
class TestOrderAndAccess:

    def test_a_bad_body_for_an_unknown_job_is_400(self, rendering):
        _job, record, client = rendering
        assert post_report(client, 999999, report_body(record, card_key='x')).status_code == 400

    @pytest.mark.parametrize('pk', [999999, 'abc', '1' * 19])
    def test_a_good_body_for_an_unknown_job_is_404(self, rendering, pk):
        _job, record, client = rendering
        resp = post_report(client, pk, report_body(record))
        assert (resp.status_code, resp.data) == (404, {'error': 'Job not found.'})

    def test_an_admin_cannot_report(self, rendering, admin_client):
        _refused((rendering[0], rendering[1], admin_client), report_body(rendering[1]), code=403)

    def test_an_anonymous_caller_cannot_report(self, rendering):
        resp = _refused((rendering[0], rendering[1], APIClient()), report_body(rendering[1]), code=403)
        assert 'detail' in resp.data

    def test_the_driver_default_is_the_records(self, rendering):
        assert report_body(rendering[1])['driver_version'] == OLD_DRIVER


@pytest.mark.django_db
class TestSchema:

    @staticmethod
    def _schema():
        resp = Client().get('/api/schema/?format=json', HTTP_ACCEPT='application/json')
        assert resp.status_code == 200
        return json.loads(resp.content.decode('utf-8'))

    def test_the_enums_are_pinned(self):
        schemas = self._schema()['components']['schemas']
        assert schemas['SpillCauseEnum']['enum'] == ['self', 'contention']
        assert schemas['MachineStateEnum']['enum'] == [
            'fits', 'does_not_fit', 'testing', 'failed', 'retest_due', 'not_tried']
        assert 'MemoryReportResultEnum' not in schemas
        result = schemas['MemoryReportResponse']['properties']['result']
        assert result['allOf'] == [{'$ref': '#/components/schemas/MachineStateEnum'}]
        cause = json.dumps(schemas['MemoryReportRequest']['properties']['spill_cause'])
        assert '#/components/schemas/SpillCauseEnum' in cause

    def test_every_key_is_required(self):
        request = self._schema()['components']['schemas']['MemoryReportRequest']
        assert set(request['required']) == set(BODY_KEYS)

    def test_the_path_is_a_worker_post(self):
        paths = self._schema()['paths']
        operation = paths['/api/jobs/{id}/memory-report/']['post']
        assert set(operation['responses']) == {'200', '400', '404', '409'}
