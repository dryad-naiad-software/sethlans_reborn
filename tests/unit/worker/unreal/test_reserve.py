# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""FR-UE-1 ``try_reserve`` checks and order, FR-UE-21 gate per kind (chunk 3 L2: no resource held on None)."""
from types import SimpleNamespace

import pytest

from sethlans_worker_agent.executors.base import ProbePending
from sethlans_worker_agent.unreal_executor import reserve, vram_gate_check, zen_port
from tests._unreal_worker_state import install, reachable
from tests.unit.worker.unreal._executor_fakes import job, params, reading


_SAME = object()


class Sampler:
    def __init__(self, latest, fresh=_SAME):
        self._latest, self._fresh, self.fresh_calls = latest, latest if fresh is _SAME else fresh, 0

    def latest(self):
        return self._latest

    def fresh(self, max_age_s, deadline):
        self.fresh_calls += 1
        return self._fresh


class Reach:
    def __init__(self, results=None):
        self.results, self.calls = results or {}, []

    def check(self, root, relpath, kind, **kwargs):
        self.calls.append((relpath, kind, kwargs['pool']))
        return self.results.get(relpath, True)


def make(**overrides):
    calls = []
    state = dict(is_idle=lambda: True, status=lambda: (True, ''), installs=lambda: (install(),),
                 sampler=lambda: Sampler(reading()), port_ok=lambda: zen_port.PortCheck(ok=True),
                 isolation_ok=lambda: True, share_state=lambda: reachable(), entry_ready=lambda sha: False,
                 reachability=Reach, resolve=lambda root, rel, kind: calls.append((rel, kind)), budget=lambda: None)
    state.update(overrides)
    reach = state['reachability']()
    state['reachability'] = lambda: reach
    return reserve.Reserver(reserve.ReserveDeps(**state)), reach


def token_free(reserver):
    if reserver.token.acquire(blocking=False):
        reserver.token.release()
        return True
    return False


def test_eligible_job_returns_a_reservation_with_validated_params():
    reserver, reach = make()
    reservation = reserver.try_reserve(job())
    assert reservation.params.share_root.canonical == '\\\\nas.lan\\sethlans'
    assert reservation.install.build == '5.8.4-58902052' and reservation.gate.ok
    assert reach.calls == [('incoming/Demo.zip', 'file', 'poll'), ('project-fixes', 'dir', 'poll')]
    assert not token_free(reserver)
    reservation.release()
    assert token_free(reserver)


@pytest.mark.parametrize('override', [
    {'is_idle': lambda: False}, {'status': lambda: (False, 'no install')},
    {'installs': lambda: (install('5.8.4-1'),)},
    {'sampler': lambda: Sampler(reading(used=25000))}, {'sampler': lambda: None},
    {'port_ok': lambda: zen_port.PortCheck(ok=False, error='x')}, {'isolation_ok': lambda: False},
    {'share_state': lambda: reachable(ok=False)},
    {'resolve': lambda root, rel, kind: (_ for _ in ()).throw(ValueError('outside'))},
    {'reachability': lambda: Reach({'project-fixes': False})},
    {'sampler': lambda: Sampler(reading(), fresh=reading(used=25000))},
    {'sampler': lambda: Sampler(reading(), fresh=None)},
])
def test_each_failed_check_declines_and_holds_nothing(override):
    reserver, _reach = make(**override)
    assert reserver.try_reserve(job()) is None
    assert token_free(reserver)


def test_invalid_job_data_is_declined_before_any_share_access():
    reserver, reach = make()
    assert reserver.try_reserve(job(renderer='BLENDER')) is None
    assert reach.calls == [] and token_free(reserver)


def test_pending_probe_raises_probe_pending_without_the_token():
    reserver, _reach = make(reachability=lambda: Reach({'incoming/Demo.zip': None}))
    with pytest.raises(ProbePending):
        reserver.try_reserve(job())
    assert token_free(reserver)


def test_ready_cache_entry_skips_the_archive_probe():
    reserver, reach = make(entry_ready=lambda sha: True)
    assert reserver.try_reserve(job()) is not None
    assert reach.calls == [('project-fixes', 'dir', 'poll')]


def test_token_is_exclusive():
    reserver, _reach = make()
    first = reserver.try_reserve(job(job_id=1))
    assert first is not None and reserver.try_reserve(job(job_id=2)) is None
    first.release()
    assert reserver.try_reserve(job(job_id=2)) is not None


def test_the_static_gate_runs_before_the_share_and_the_fresh_reading_after_the_probes():
    sampler = Sampler(reading(used=25000))
    reserver, reach = make(sampler=lambda: sampler)
    assert reserver.try_reserve(job()) is None
    assert reach.calls == [] and sampler.fresh_calls == 0


class TestGate:
    """FR-UE-21 worked examples (PoC caps 9,000 + 2,048; 1,000 MB in use)."""

    @pytest.mark.parametrize('total,ok', [(32607, True), (20464, True), (16303, True), (12282, False)])
    def test_pilot_relaxed_gate(self, total, ok):
        result = vram_gate_check.evaluate(params(kind='pilot'), reading(total=total))
        assert result.ok is ok and result.need_mb == 12478 and result.gate == 'pilot'

    def test_pilot_needs_nvidia_smi(self):
        result = vram_gate_check.evaluate(params(kind='pilot'), reading(source='override'))
        assert not result.ok and 'nvidia-smi' in result.message

    @pytest.mark.parametrize('total,ok,text', [
        (32607, True, ''), (16303, True, ''), (12282, True, ''),
        (11264, False, 'pool caps 11048 MB exceed the 10475 MB budget of a 11264 MB GPU.'),
        (10240, False, 'needs 9120 MB; 8523 MB usable on a 10240 MB GPU (1000 MB in use).'),
    ])
    def test_measured_gate(self, total, ok, text):
        result = vram_gate_check.evaluate(params(peak=7930), reading(total=total))
        assert result.ok is ok and result.message == text and result.need_mb == 9120

    def test_every_gpu_must_fit_and_the_summary_names_the_smallest(self):
        from sethlans_worker_agent.unreal_executor.vram import GpuMem
        two = reading(gpus=(GpuMem(0, 'a', 32607, 1000, None, None), GpuMem(1, 'b', 16303, 1000, None, None)))
        result = vram_gate_check.evaluate(params(peak=7930), two)
        assert result.ok and result.summary['vram_mb'] == 16303 and result.summary['vram_measured_peak_mb'] == 7930
        busy = reading(gpus=(GpuMem(0, 'a', 32607, 1000, None, None), GpuMem(1, 'b', 16303, 9000, None, None)))
        assert not vram_gate_check.evaluate(params(peak=7930), busy).ok

    def test_no_reading(self):
        assert not vram_gate_check.evaluate(params(), None).ok
        assert not vram_gate_check.evaluate(params(), SimpleNamespace(gpus=(), source='nvidia-smi')).ok
