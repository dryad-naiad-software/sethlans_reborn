# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Builders for the memory report unit tests (per-machine memory test FR-MT-S-3, FR-MT-S-4). Not a test module."""
from types import SimpleNamespace

from sethlans_worker_agent import api_extras
from sethlans_worker_agent.unreal_executor import machine_results, spill
from sethlans_worker_agent.unreal_executor.runner_types import VramTrace
from sethlans_worker_agent.unreal_executor.summary import Summary
from tests.unit.worker.unreal._executor_fakes import KIND, machine_result, outcome, params

CARD_KEY = 'pci:0000:41:00.0'
DRIVER = '32.0.22029.1019'
P = '[2026.11.17-18.27.59:807][  0]'


def log_text(driver=DRIVER):
    """A render log naming adapter 1 and its driver (``driver_log.launch_driver``)."""
    line = f'{P}LogD3D12RHI:   Driver Version: 25.10.1 (internal:{driver}, unified:25.10.1)\n' if driver else ''
    return (f'{P}LogD3D12RHI: Found D3D12 adapter 1: Radeon Test 24G (VendorId: 1002, DeviceId: 744c\n' + line
            + f'{P}LogD3D12RHI: Chosen D3D12 Adapter Id = 1\n')


def result(measurement_id=7, driver_version=DRIVER, shared_peak_mb=1000):
    raw = machine_result(measurement_id=measurement_id, shared_peak_mb=shared_peak_mb, driver_version=driver_version)
    return machine_results.parse('render', [raw])[0]


def watch(spilled=None, cause=None, shared=None, reference=1000):
    """A ``SpillWatch`` in the given end state (set directly: the watch's rules have their own tests)."""
    w = spill.SpillWatch(reference_mb=reference, used_at_gate_mb=3000, margin_mb=256)
    if spilled is not None:
        w.samples = 1
        w.spilled_seen = spilled
        w.contention_seen = cause == spill.CONTENTION
    w.shared_peak_mb = shared
    return w


def render_run(tmp_path, attempt=1, max_job=None, spill_watch=None, driver=DRIVER, phase='render', max_used=None):
    """A render launch: ``max_job`` the members' MiB on the card over the whole launch, ``max_used`` the card's."""
    trace = VramTrace(spill=spill_watch)
    if max_job is not None:
        trace.max_job[1] = max_job
    if max_used is not None:
        trace.max_used[1], trace.min_used[1], trace.total[1] = max_used, 500, 24560
    log = tmp_path / f'{phase}.{attempt}.log'
    log.write_text(log_text(driver), encoding='utf-8')
    return SimpleNamespace(phase=phase, attempt=attempt, pre={1: 500}, post={1: 500}, log_path=log,
                           outcome=outcome(vram=trace))


class Reports:
    """``report_memory`` fake: records ``(job_id, body, kwargs)`` and answers ``answer`` (or raises it)."""

    def __init__(self, answer=None):
        self.answer = answer or api_extras.PilotReport('done', 200)
        self.calls = []

    def __call__(self, job_id, body, **kwargs):
        self.calls.append((job_id, body, kwargs))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


_DEFAULT = object()


def job_run(runs, *, kind='render', p=None, gate_result=_DEFAULT, card_kind=KIND, card_key=CARD_KEY, cause='',
            report=None, flow=True):
    """What ``memory_report`` reads from a ``JobRun``."""
    gate = SimpleNamespace(result=result() if gate_result is _DEFAULT else gate_result, card_kind=card_kind,
                           used_mb=3000, card_key=card_key)
    summary = Summary()
    flow_ns = SimpleNamespace(choice=None, warmup_logs=[], summary=summary) if flow else None
    return SimpleNamespace(params=p or params(kind=kind), reservation=SimpleNamespace(gate=gate, card_key=card_key),
                           runner=SimpleNamespace(runs=list(runs)), flow=flow_ns, summary=summary,
                           cause=lambda: cause,
                           deps=SimpleNamespace(delivery=SimpleNamespace(report_memory=report or Reports())))
