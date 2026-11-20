# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the GPU memory reader manager tests (gpu-memory-reader
chunk 1, FR-GH-1..3, FR-GH-10, FR-GT-5).

Not a test module.  Heartbeat and pilot-result bodies come from the worker's
own builders (``vram.to_payload``, ``pilot.report_body``) and travel through
the real ``/api/heartbeat/`` and ``/api/jobs/{id}/pilot-result/`` endpoints.
All card names and keys are made up.
"""

import json

from shared.unreal.validation import overrides_sha256
from sethlans_worker_agent.gpu_memory.types import GpuMemoryReading, make_gpu
from sethlans_worker_agent.unreal_executor import pilot, vram

from .unreal_helpers import BUILD, SHARE, caps, set_share

HB = '/api/heartbeat/'
NO_READING = "Worker cannot measure GPU memory (no live card reading)."
NV_KEY = 'pci:0000:0a:00.0'
AMD_KEY = 'pci:0000:2c:00.0'
NV_NAME = 'Example NV-like Graphics 16G'
AMD_NAME = 'Example AMD-like Graphics 20G'
NV_TOTAL = 16303
AMD_TOTAL = 20464


def nv_gpu(index=0, card_key=NV_KEY, total_mb=NV_TOTAL):
    return make_gpu(index, NV_NAME, total_mb, 1024, 256, card_key=card_key)


def amd_gpu(index=1, card_key=AMD_KEY, total_mb=AMD_TOTAL):
    return make_gpu(index, AMD_NAME, total_mb, 2048, 512, card_key=card_key)


def reading(*gpus, source='nvidia-smi', card=True, process=True):
    """A worker-side ``GpuMemoryReading`` (defaults to one NV-like card)."""
    return GpuMemoryReading(taken_at=100.0, source=source, gpus=tuple(gpus) or (nv_gpu(),),
                            measures_card=card, measures_process=process)


def wire(payload):
    """The value as the manager receives it (through JSON)."""
    return json.loads(json.dumps(payload))


def payload_of(gpu_reading):
    """The worker's heartbeat ``gpu_memory`` for ``gpu_reading``."""
    return wire(vram.to_payload(gpu_reading, now=103.0))


def send_heartbeat(client, worker, gpu_memory):
    """POST one Unreal-ready heartbeat carrying ``gpu_memory``; return the stored reading."""
    set_share(SHARE)
    resp = client.post(HB, {'hostname': worker.hostname, 'unreal_capabilities': caps(),
                            'gpu_memory': gpu_memory}, format='json')
    assert resp.status_code == 200, resp.data
    worker.refresh_from_db()
    return worker.gpu_memory


def metrics():
    return pilot.PilotMetrics(
        peak_mb=8102, peak_source='csv', card_delta_peak_mb=7930, csv_local_used_peak_mb=8102,
        baseline_used_mb=945, baseline_drift_mb=12, peak_phase='render', gpu_index=0,
        max_sample_gap_s=1.2, process_peak_mb=6144)


def report(gpu):
    """The worker's own pilot-result body for a pilot that ran on ``gpu``."""
    return wire(pilot.report_body(
        metrics(), gpu, prep_s=24.1, warmup_s=800.2, render_s=1380.5, frames=23,
        engine_build=BUILD, overrides_sha256=overrides_sha256({}), summary='pilot_frames=23'))
