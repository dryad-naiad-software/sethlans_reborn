# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the held job of each reported card (Unreal render per
card FR-PC-MGR-3, chunk 6).

``job_id`` and ``renderer`` are *nullable on invalid*: an invalid value is
stored ``null`` and the card is kept, and the two are a pair, both
``null`` unless both are valid.  Checked through the real heartbeat
endpoint, the stored row, the heartbeat answer, the admin listing and the
OpenAPI schema.
"""

import json

import pytest
from django.test import Client

from tests.unit.manager.card_report_helpers import KEY_2, card, report
from workers.models import Worker

HB = '/api/heartbeat/'
MAX_JOB_ID = 2 ** 31 - 1
FREE = {'job_id': None, 'renderer': None}


def _send(make_worker_client, hostname, *cards):
    """Post one heartbeat carrying ``cards``; return the stored and the answered card lists."""
    worker, client, _token = make_worker_client(hostname)
    resp = client.post(HB, {'hostname': hostname, 'cards': report(cards=list(cards))}, format='json')
    assert resp.status_code == 200, resp.data
    stored = Worker.objects.get(pk=worker.pk).card_status['cards']
    assert resp.data['card_status']['cards'] == stored
    return stored


def _pair(entry):
    return {'job_id': entry['job_id'], 'renderer': entry['renderer']}


@pytest.mark.django_db
class TestHeldJobPair:

    @pytest.mark.parametrize('job_id, renderer', [
        (1, 'BLENDER'), (MAX_JOB_ID, 'UNREAL'), (None, None),
    ], ids=['blender-1', 'unreal-max', 'free'])
    def test_a_valid_pair_is_kept(self, make_worker_client, job_id, renderer):
        stored = _send(make_worker_client, 'held-ok', card(job_id=job_id, renderer=renderer))
        assert stored == [card(job_id=job_id, renderer=renderer)]

    @pytest.mark.parametrize('job_id', [
        0, -1, MAX_JOB_ID + 1, True, False, '5', 5.0, [5], {'id': 5},
    ], ids=['zero', 'negative', 'too-big', 'true', 'false', 'string', 'float', 'list', 'dict'])
    def test_an_invalid_job_id_nulls_the_pair_and_keeps_the_card(self, make_worker_client, job_id):
        stored = _send(make_worker_client, 'held-bad-id', card(job_id=job_id, renderer='UNREAL'), card(key=KEY_2))
        assert stored == [card(**FREE), card(key=KEY_2)]

    @pytest.mark.parametrize('renderer', [
        'blender', 'Unreal', 'CYCLES', '', 1, True, ['UNREAL'],
    ], ids=['lower', 'title', 'engine', 'empty', 'int', 'bool', 'list'])
    def test_an_invalid_renderer_nulls_the_pair_and_keeps_the_card(self, make_worker_client, renderer):
        stored = _send(make_worker_client, 'held-bad-renderer', card(job_id=42, renderer=renderer))
        assert stored == [card(**FREE)]

    @pytest.mark.parametrize('job_id, renderer', [
        (None, 'UNREAL'), (42, None),
    ], ids=['no-job', 'no-renderer'])
    def test_one_null_nulls_both(self, make_worker_client, job_id, renderer):
        stored = _send(make_worker_client, 'held-one-null', card(job_id=job_id, renderer=renderer))
        assert [_pair(entry) for entry in stored] == [FREE]

    @pytest.mark.parametrize('absent', ['job_id', 'renderer'])
    def test_one_absent_nulls_both(self, make_worker_client, absent):
        entry = card(job_id=42, renderer='BLENDER')
        del entry[absent]
        stored = _send(make_worker_client, 'held-one-absent', entry)
        assert [_pair(entry) for entry in stored] == [FREE]

    def test_pairs_are_per_card(self, make_worker_client):
        stored = _send(
            make_worker_client, 'held-two',
            card(job_id=7, renderer='UNREAL'), card(key=KEY_2, job_id=8, renderer='UNREAL'),
        )
        assert [_pair(entry) for entry in stored] == [
            {'job_id': 7, 'renderer': 'UNREAL'}, {'job_id': 8, 'renderer': 'UNREAL'},
        ]

    def test_the_admin_listing_shows_the_pair(self, make_worker_client, admin_client):
        _send(make_worker_client, 'held-list', card(job_id=9, renderer='BLENDER'))
        row = next(w for w in admin_client.get(HB).data if w['hostname'] == 'held-list')
        assert _pair(row['card_status']['cards'][0]) == {'job_id': 9, 'renderer': 'BLENDER'}


@pytest.mark.django_db
class TestOpenAPI:

    @pytest.fixture
    def comps(self):
        resp = Client().get('/api/schema/?format=json', HTTP_ACCEPT='application/json')
        assert resp.status_code == 200
        return json.loads(resp.content.decode('utf-8'))['components']['schemas']

    def test_card_documents_an_optional_nullable_pair(self, comps):
        props = comps['WorkerCard']['properties']
        assert 'required' not in comps['WorkerCard']
        assert props['job_id'] == {'type': 'integer', 'nullable': True}
        assert props['renderer']['nullable'] is True
        assert {'$ref': '#/components/schemas/RendererEnum'} in props['renderer']['oneOf']

    def test_renderer_enum_name_is_pinned_and_shared(self, comps):
        assert comps['RendererEnum']['enum'] == ['BLENDER', 'UNREAL']
        assert not [name for name in comps if 'Renderer' in name and name.endswith('Enum') and name != 'RendererEnum']
