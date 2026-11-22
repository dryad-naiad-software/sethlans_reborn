# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Contract: the worker's real memory report against the manager's real
``memory-report/`` (Unreal per-machine memory test FR-MT-S-3, FR-MT-S-4,
D3, D9, FR-MT-G-1).

The round trip uses each side's own code: the manager's job payload
(``unreal_machine_results`` for the worker) is parsed by the worker's
``machine_results.parse`` and picked by ``for_kind`` into a real
``GateResult``; the render launch's real ``VramTrace`` is fed real
``GpuMemoryReading`` samples (per-pid dedicated and shared usage) with
the real ``spill.watch_for`` watch; the driver is read from a real UE log
fixture (``tests/unit/worker/unreal/fixtures/d3d12_logs``); the body is
built by ``memory_report.body`` and posted through JSON to the endpoint.
Only the job run's plain containers are namespaces, with the attribute
names ``memory_report`` and ``pilot_flow.driver`` read.

Covers: a plain fitting piece (200 ``fits``, the job peak raised); a
``self`` spill (200 ``does_not_fit``, the version warning); a run with an
unknown driver, no per-pid attribution and no shared counter (every
nullable field null, accepted, nothing marked or staled).
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import config_unreal
from sethlans_worker_agent.gpu_memory.types import GpuMemoryReading, make_gpu
from sethlans_worker_agent.unreal_executor import machine_results, memory_report, spill
from sethlans_worker_agent.unreal_executor.phases import PhaseRun
from sethlans_worker_agent.unreal_executor.runner_types import RunOutcome, VramTrace
from sethlans_worker_agent.unreal_executor.summary import Summary
from sethlans_worker_agent.unreal_executor.vram_gate_check import GateResult
from workers.models import UnrealMeasurement

from .machine_verdict_helpers import NEW_DRIVER, TOTAL
from .memory_report_helpers import BODY_KEYS, CARD_KEY, accepted, piece_record, spill_text, state, warnings_of
from .sequence.submission_helpers import claimed_piece
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import JOBS, KIND

LOGS = Path(__file__).resolve().parents[2] / 'unit' / 'worker' / 'unreal' / 'fixtures' / 'd3d12_logs'
NV_LOG = LOGS / 'nvidia.txt'              # chosen adapter's internal driver 32.0.16.1692 (NEW_DRIVER)
UNKNOWN_LOG = LOGS / 'driver_unknown.txt'  # chosen adapter's internal driver Unknown
OWN = frozenset({4100, 4101})
OTHER_PID = 9000
USED_AT_GATE = 1000
REFERENCE = 128  # the result's shared_peak_mb (the pilot's own figure)


@pytest.fixture
def piece(admin_client, worker_with_token, settings, tmp_path, monkeypatch):
    """A piece RENDERING on the worker; its piece-key result carries the driver and the shared reference."""
    monkeypatch.setattr(config_unreal, 'SPILL_MARGIN_MB', 256)
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    worker, client = worker_with_token
    submission, job = claimed_piece(admin_client, unreal_asset(), worker, client)
    record = piece_record(submission, worker)
    UnrealMeasurement.objects.filter(pk=record.pk).update(driver_version=NEW_DRIVER, shared_peak_mb=REFERENCE)
    return submission, job, record, worker, client


def _gate(client, job, reference=REFERENCE):
    """The worker's ``GateResult`` from the manager's own job payload (FR-MT-G-1, D7)."""
    if reference != REFERENCE:
        UnrealMeasurement.objects.filter(pk__in=[r['measurement_id'] for r in _results(client, job)]).update(
            shared_peak_mb=reference)
    result = machine_results.for_kind(machine_results.parse('render', _results(client, job)), KIND)
    assert result is not None
    return GateResult(ok=True, gate='measured', need_mb=result.peak_mb, card_key=CARD_KEY, card_kind=KIND,
                      used_mb=USED_AT_GATE, result=result)


def _results(client, job):
    resp = client.get(f'{JOBS}{job.pk}/')
    assert resp.status_code == 200, resp.data
    return json.loads(json.dumps(resp.data['unreal_machine_results']))


def _reading(at, dedicated, shared, attributed=True, shared_counter=True):
    """One sample of the job's card: its members' dedicated and shared MiB, another program's 1,000 MB."""
    gpu = make_gpu(0, 'Example GPU', TOTAL, dedicated + USED_AT_GATE, None, card_key=CARD_KEY)
    per_pid = {CARD_KEY: {4100: dedicated, OTHER_PID: USED_AT_GATE}} if attributed else None
    per_pid_shared = {CARD_KEY: {4100: shared, OTHER_PID: 50}} if shared_counter else None
    return GpuMemoryReading(taken_at=at, source='pdh', gpus=(gpu,), measures_card=True,
                            measures_process=attributed, per_pid=per_pid, per_pid_shared=per_pid_shared)


def _job_run(job, gate, samples, log_path, **flags):
    """The job run ``memory_report.body`` reads: one render launch whose trace saw ``samples``."""
    trace = VramTrace(spill=spill.watch_for(gate, job.pk)())
    for at, (dedicated, shared) in enumerate(samples, start=1):
        trace.add(_reading(float(at), dedicated, shared, **flags), OWN, window=True)
    outcome = RunOutcome(exit_code=0, reason='ok', rule_counts={}, wall_s=30.0, survivors=(), phase='render',
                         vram=trace)
    run = PhaseRun(phase='render', attempt=1, outcome=outcome, log_path=log_path)
    return SimpleNamespace(
        reservation=SimpleNamespace(gate=gate, card_key=CARD_KEY), runner=SimpleNamespace(runs=[run]),
        flow=SimpleNamespace(choice=None, warmup_logs=(), summary=Summary()), summary=Summary(),
        params=SimpleNamespace(job_id=job.pk))


def _worker_body(job_run):
    body = json.loads(json.dumps(memory_report.body(job_run)))
    assert set(body) == set(BODY_KEYS)
    return body


@pytest.mark.django_db
class TestWorkerBodyAgainstEndpoint:

    def test_a_plain_fitting_piece(self, piece):
        submission, job, record, _worker, client = piece
        run = _job_run(job, _gate(client, job), [(7000, 100), (7100, 120)], NV_LOG)
        body = _worker_body(run)
        assert body == {'measurement_id': record.pk, 'card_kind': KIND, 'card_key': CARD_KEY,
                        'driver_version': NEW_DRIVER, 'spilled': False, 'spill_cause': None,
                        'shared_peak_mb': 120, 'job_peak_mb': 7100}
        accepted(client, job, body, result='fits')
        assert state(record)[:4] == ('fits', 'pilot', '', 7100)
        assert run.summary.get('driver_changed') == '0'
        assert warnings_of(submission) == []

    def test_a_self_spill(self, piece):
        submission, job, record, worker, client = piece
        run = _job_run(job, _gate(client, job), [(7000, 100), (7200, 1900), (7300, 2100)], NV_LOG)
        body = _worker_body(run)
        assert (body['spilled'], body['spill_cause'], body['shared_peak_mb']) == (True, 'self', 2100)
        accepted(client, job, body, result='does_not_fit')
        assert state(record)[:2] == ('does_not_fit', 'spill')
        assert warnings_of(submission) == [spill_text(worker.hostname, job.name)]

    def test_unknown_driver_and_no_counters_send_nulls(self, piece):
        submission, job, record, _worker, client = piece
        before = state(record)
        run = _job_run(job, _gate(client, job, reference=None), [(7000, 100), (7100, 1900)], UNKNOWN_LOG,
                       attributed=False, shared_counter=False)
        body = _worker_body(run)
        assert body == {'measurement_id': record.pk, 'card_kind': KIND, 'card_key': CARD_KEY,
                        'driver_version': None, 'spilled': None, 'spill_cause': None,
                        'shared_peak_mb': None, 'job_peak_mb': None}
        accepted(client, job, body, result='fits')
        assert state(record) == before
        assert warnings_of(submission) == []
