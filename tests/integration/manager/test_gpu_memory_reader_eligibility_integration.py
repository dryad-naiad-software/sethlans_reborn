# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: which heartbeats make a worker eligible for pilots
(gpu-memory-reader FR-GH-2, FR-GH-4, FR-GT-5, AC-10, AC-26).

Every reading goes through the real ``/api/heartbeat/``; eligibility is then
checked on both the worker's poll and its claim of the pilot.
"""

import pytest

from .gpu_memory_reader_helpers import (
    AMD_KEY, AMD_NAME, AMD_TOTAL, NO_READING, NV_KEY, NV_NAME, NV_TOTAL,
    amd_gpu, nv_gpu, payload_of, reading, send_heartbeat,
)
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import JOBS, POLL, claim, created_job, pilots


def _offered(client):
    return [job['name'] for job in client.get(JOBS, POLL).data]


def _pilot(admin_client):
    created_job(admin_client, unreal_asset())
    return pilots()[0]


def _assert_no_pilot(admin_client, worker, client):
    job = _pilot(admin_client)
    assert _offered(client) == []
    resp = claim(client, job, worker)
    assert resp.status_code != 200
    assert resp.data == {'error': NO_READING}
    job.refresh_from_db()
    assert (job.status, job.assigned_worker_id) == ('QUEUED', None)


def _assert_pilot_offered(admin_client, worker, client):
    job = _pilot(admin_client)
    assert _offered(client) == [job.name]
    assert claim(client, job, worker).status_code == 200
    job.refresh_from_db()
    assert (job.status, job.assigned_worker_id) == ('RENDERING', worker.pk)


def _all_invalid_payload():
    payload = payload_of(reading(nv_gpu(), amd_gpu()))
    payload['gpus'][0]['index'] = -1
    payload['gpus'][1]['total_mb'] = 'big'
    return payload


@pytest.mark.django_db
class TestCardReadingIsEligibleIntegration:

    def test_nvidia_smi_card_reading_with_valid_key_is_offered_the_pilot(self, admin_client, worker_with_token):
        worker, client = worker_with_token
        stored = send_heartbeat(client, worker, payload_of(reading(nv_gpu())))
        assert stored['capabilities'] == {'card': True, 'process': True}
        assert [gpu['card_key'] for gpu in stored['gpus']] == [NV_KEY]
        _assert_pilot_offered(admin_client, worker, client)

    @pytest.mark.parametrize('source', ['pdh', 'mixed', 'amdgpu-sysfs'])
    def test_mixed_brands_under_a_card_measuring_source_are_eligible(self, admin_client, worker_with_token, source):
        worker, client = worker_with_token
        stored = send_heartbeat(client, worker, payload_of(reading(nv_gpu(), amd_gpu(), source=source)))
        assert (stored['source'], stored['capabilities']) == (source, {'card': True, 'process': True})
        assert [(g['index'], g['name'], g['total_mb'], g['card_key']) for g in stored['gpus']] == [
            (0, NV_NAME, NV_TOTAL, NV_KEY), (1, AMD_NAME, AMD_TOTAL, AMD_KEY)]
        _assert_pilot_offered(admin_client, worker, client)

    def test_one_invalid_entry_among_valid_ones_keeps_the_card_reading(self, admin_client, worker_with_token):
        worker, client = worker_with_token
        payload = payload_of(reading(nv_gpu(), amd_gpu(), source='mixed'))
        payload['gpus'][0]['total_mb'] = 0
        stored = send_heartbeat(client, worker, payload)
        assert stored['capabilities']['card'] is True
        assert [gpu['card_key'] for gpu in stored['gpus']] == [AMD_KEY]
        _assert_pilot_offered(admin_client, worker, client)


@pytest.mark.django_db
class TestNoCardReadingIntegration:
    """AC-26 end to end, plus the other FR-GT-5 "no pilots" readings."""

    def test_override_sending_card_true_is_stored_false_and_gets_no_pilot(self, admin_client, worker_with_token):
        worker, client = worker_with_token
        payload = payload_of(reading(nv_gpu(card_key=None), source='override', card=True, process=False))
        assert payload['capabilities']['card'] is True  # guard: the body really claims a card reading
        stored = send_heartbeat(client, worker, payload)
        assert (stored['source'], stored['capabilities']) == ('override', {'card': False, 'process': False})
        assert len(stored['gpus']) == 1
        _assert_no_pilot(admin_client, worker, client)

    def test_override_with_a_valid_card_key_still_gets_no_pilot(self, admin_client, worker_with_token):
        worker, client = worker_with_token
        stored = send_heartbeat(client, worker, payload_of(reading(nv_gpu(), source='override')))
        assert stored['capabilities']['card'] is False
        _assert_no_pilot(admin_client, worker, client)

    def test_every_gpu_entry_invalid_is_stored_false_and_gets_no_pilot(self, admin_client, worker_with_token):
        worker, client = worker_with_token
        payload = _all_invalid_payload()
        assert payload['capabilities']['card'] is True  # guard
        stored = send_heartbeat(client, worker, payload)
        assert (stored['gpus'], stored['capabilities']) == ([], {'card': False, 'process': True})
        _assert_no_pilot(admin_client, worker, client)

    def test_card_false_gets_no_pilot(self, admin_client, worker_with_token):
        worker, client = worker_with_token
        stored = send_heartbeat(client, worker, payload_of(reading(nv_gpu(), source='pdh', card=False)))
        assert stored['capabilities']['card'] is False
        _assert_no_pilot(admin_client, worker, client)

    @pytest.mark.parametrize('capabilities', ['absent', None, {'card': 'yes', 'process': True}, {'card': True}])
    def test_missing_or_invalid_capabilities_get_no_pilot(self, admin_client, worker_with_token, capabilities):
        worker, client = worker_with_token
        payload = payload_of(reading(nv_gpu()))
        if capabilities == 'absent':
            del payload['capabilities']
        else:
            payload['capabilities'] = capabilities
        stored = send_heartbeat(client, worker, payload)
        assert 'capabilities' not in stored
        assert [gpu['card_key'] for gpu in stored['gpus']] == [NV_KEY]
        _assert_no_pilot(admin_client, worker, client)

    def test_a_later_override_heartbeat_withdraws_the_pilot(self, admin_client, worker_with_token):
        worker, client = worker_with_token
        send_heartbeat(client, worker, payload_of(reading(nv_gpu())))
        job = _pilot(admin_client)
        assert _offered(client) == [job.name]
        send_heartbeat(client, worker, payload_of(reading(nv_gpu(card_key=None), source='override')))
        assert _offered(client) == []
        assert claim(client, job, worker).data == {'error': NO_READING}
