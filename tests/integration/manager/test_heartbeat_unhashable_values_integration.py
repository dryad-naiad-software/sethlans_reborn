# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: heartbeat values that are a JSON list or object where a
string is expected (GitHub issue #279).

``gpu_memory.source``, an Unreal install's ``source`` and ``status`` used to
reach a ``frozenset`` membership test and end in a 500.  Through the real
``/api/heartbeat/`` each must be treated like any other invalid value.
"""

import pytest

from workers.models import Worker

from .gpu_memory_reader_helpers import HB, NV_KEY, payload_of, reading, send_heartbeat
from .unreal_helpers import BUILD, SHARE, caps, set_share

UNHASHABLE = [['nvidia-smi'], {'nvidia-smi': True}]
IDS = ['list', 'dict']


def _post(client, worker, **fields):
    return client.post(HB, {'hostname': worker.hostname, **fields}, format='json')


@pytest.mark.django_db
class TestHeartbeatUnhashableValuesIntegration:

    @pytest.mark.parametrize('source', UNHASHABLE, ids=IDS)
    def test_gpu_memory_source_is_dropped_and_heartbeat_accepted(self, worker_with_token, source):
        worker, client = worker_with_token
        set_share(SHARE)
        payload = payload_of(reading())
        payload['source'] = source
        resp = _post(client, worker, unreal_capabilities=caps(), gpu_memory=payload)
        assert resp.status_code == 200, resp.data
        worker.refresh_from_db()
        assert worker.gpu_memory == {}
        assert [i['build'] for i in worker.unreal_capabilities['installs']] == [BUILD]

    @pytest.mark.parametrize('source', UNHASHABLE, ids=IDS)
    def test_gpu_memory_source_replaces_an_earlier_reading(self, worker_with_token, source):
        worker, client = worker_with_token
        assert send_heartbeat(client, worker, payload_of(reading()))['gpus'][0]['card_key'] == NV_KEY
        payload = payload_of(reading())
        payload['source'] = source
        resp = _post(client, worker, unreal_capabilities=caps(), gpu_memory=payload)
        assert resp.status_code == 200, resp.data
        worker.refresh_from_db()
        assert worker.gpu_memory == {}

    @pytest.mark.parametrize('source', [['launcher'], {'launcher': 1}], ids=IDS)
    def test_install_source_is_dropped_and_heartbeat_accepted(self, worker_with_token, source):
        worker, client = worker_with_token
        set_share(SHARE)
        unreal = caps(builds=(BUILD, '5.7.1-1'))
        unreal['installs'][1]['source'] = source
        resp = _post(client, worker, unreal_capabilities=unreal, gpu_memory=payload_of(reading()))
        assert resp.status_code == 200, resp.data
        worker.refresh_from_db()
        assert worker.unreal_capabilities['installs'] == [{'build': BUILD, 'supported': True, 'source': 'launcher'}]
        assert worker.gpu_memory['gpus'][0]['card_key'] == NV_KEY

    @pytest.mark.parametrize('status', [['IDLE'], {'IDLE': True}], ids=IDS)
    def test_status_is_handled_like_any_other_invalid_status(self, worker_with_token, status):
        worker, client = worker_with_token
        baseline = _post(client, worker, status='NOT_A_STATUS')
        assert baseline.status_code == 200, baseline.data
        expected = Worker.objects.get(pk=worker.pk).status
        resp = _post(client, worker, status=status)
        assert resp.status_code == baseline.status_code, resp.data
        assert set(resp.data) == set(baseline.data)
        assert Worker.objects.get(pk=worker.pk).status == expected
