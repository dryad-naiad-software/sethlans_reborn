# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Contract: the GPU memory reader's worker payloads (gpu-memory-reader chunk 2,
FR-GU-7, FR-GU-9) against the manager of chunk 1 (FR-GH-1..3, FR-GH-10):

* the heartbeat ``gpu_memory`` with ``capabilities`` and per-GPU ``card_key``
  (``vram.to_payload``) is stored with both, and the worker is offered pilots
  (``capabilities.card`` is true);
* the pilot result (``pilot.report_body``, whose ``gpu`` carries ``card_key``)
  with a summary carrying ``pilot_process_peak_mb``
  (``pilot.summary_values``) is stored.

The bodies come from the worker's own builders, so a drift in the worker
shape shows up here.
"""

import json

import pytest

from shared.unreal.validation import overrides_sha256
from sethlans_worker_agent.gpu_memory.types import GpuMemoryReading, make_gpu
from sethlans_worker_agent.unreal_executor import pilot, vram
from sethlans_worker_agent.unreal_executor.summary import Summary
from workers.models import Worker

from .unreal_helpers import BUILD, SHARE, caps, set_share, unreal_asset
from .unreal_pilot_helpers import JOBS, POLL, claim, created_job, pilots, post_result, record_of

HB = '/api/heartbeat/'
CARD_KEY = 'pci:0000:0a:00.0'
CARD_NAME = 'Example Graphics 24G'
TOTAL_MB = 24576


def _reading():
    gpu = make_gpu(0, CARD_NAME, TOTAL_MB, 2048, 512, card_key=CARD_KEY)
    return GpuMemoryReading(taken_at=100.0, source='nvidia-smi', gpus=(gpu,),
                            measures_card=True, measures_process=True)


def _wire(payload):
    """The value as the manager receives it (through JSON)."""
    return json.loads(json.dumps(payload))


def _heartbeat_payload():
    payload = _wire(vram.to_payload(_reading(), now=103.0))
    # Guard: the worker really sends the chunk-2 shape this test is about.
    assert payload['capabilities'] == {'card': True, 'process': True}
    assert payload['gpus'][0]['card_key'] == CARD_KEY
    return payload


def _metrics():
    return pilot.PilotMetrics(
        peak_mb=8102, peak_source='csv', card_delta_peak_mb=7930, csv_local_used_peak_mb=8102,
        baseline_used_mb=945, baseline_drift_mb=12, peak_phase='render', gpu_index=0,
        max_sample_gap_s=1.2, process_peak_mb=6144)


def _pilot_body(metrics):
    summary = Summary()
    summary.update(pilot.summary_values(metrics, list(range(23)), CARD_NAME, 'ef' * 32))
    body = pilot.report_body(metrics, _reading().gpus[0], prep_s=24.1, warmup_s=800.2, render_s=1380.5,
                             frames=23, engine_build=BUILD, overrides_sha256=overrides_sha256({}),
                             summary=summary.text(), game_overrides='implicit', texture_streaming='off')
    body = _wire(body)
    # Guard: the worker sends what the pilot ran with (gpu-memory-reader FR-GC-8).
    assert (body['game_overrides'], body['texture_streaming']) == ('implicit', 'off')
    # Guard: the worker sends the card key the manager matches on (FR-GN-5, FR-GH-10).
    assert body['gpu'] == {'index': 0, 'name': CARD_NAME, 'total_mb': TOTAL_MB, 'card_key': CARD_KEY}
    assert 'pilot_process_peak_mb=6144' in body['summary']
    return body


@pytest.fixture
def heartbeat(worker_with_token):
    """The worker after one chunk-2 heartbeat; returns ``(worker, client, response)``."""
    worker, client = worker_with_token
    set_share(SHARE)
    resp = client.post(HB, {'hostname': worker.hostname, 'unreal_capabilities': caps(),
                            'gpu_memory': _heartbeat_payload()}, format='json')
    assert resp.status_code == 200, resp.data
    worker.refresh_from_db()
    return worker, client, resp


@pytest.mark.django_db
class TestUnrealGpuMemoryReaderCompatIntegration:

    def test_heartbeat_is_stored_with_capabilities_and_card_key(self, heartbeat):
        worker, _client, _resp = heartbeat
        sent = _heartbeat_payload()
        assert Worker.objects.get(pk=worker.pk).gpu_memory == {
            'schema': 1, 'source': 'nvidia-smi', 'age_s': sent['age_s'],
            'capabilities': {'card': True, 'process': True},
            'gpus': [{'index': 0, 'name': CARD_NAME, 'total_mb': TOTAL_MB, 'used_mb': 2048,
                      'sethlans_used_mb': 512, 'other_used_mb': 1536, 'card_key': CARD_KEY}],
        }

    def test_worker_is_offered_pilots(self, admin_client, heartbeat):
        worker, client, _resp = heartbeat
        created_job(admin_client, unreal_asset())
        offered = [job['name'] for job in client.get(JOBS, POLL).data]
        assert offered == [pilots()[0].name]
        assert claim(client, pilots()[0], worker).status_code == 200

    def test_pilot_result_round_trip(self, admin_client, heartbeat):
        worker, client, _resp = heartbeat
        created_job(admin_client, unreal_asset())
        job = pilots()[0]
        assert claim(client, job, worker).status_code == 200
        body = _pilot_body(_metrics())
        resp = post_result(client, job.pk, body)
        assert (resp.status_code, resp.data) == (200, {'status': 'DONE', 'peak_mb': 8102, 'gate_need_mb': 9318})
        job.refresh_from_db()
        assert (job.status, job.last_output) == ('DONE', body['summary'])
        record = record_of(job)
        assert (record.peak_mb, record.card_delta_peak_mb, record.csv_local_used_peak_mb) == (8102, 7930, 8102)
        assert (record.worker_id, record.gpu_index, record.gpu_name, record.gpu_total_mb) == (
            worker.pk, 0, CARD_NAME, TOTAL_MB)
        assert (record.game_overrides, record.texture_streaming) == ('implicit', 'off')
