# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""FR-UE-1 ``try_reserve`` checks and order, FR-UE-21 gate per kind (chunk 3 L2: no resource held on None)."""
from types import SimpleNamespace

import pytest

from sethlans_worker_agent.executors.base import ProbePending
from sethlans_worker_agent.unreal_executor import reserve, vram_gate_check, zen_port
from sethlans_worker_agent.unreal_executor.slot import SLOT_COUNT
from tests._unreal_worker_state import install, reachable
from tests.unit.worker.unreal._executor_fakes import KIND, job, params, reading


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
    state = dict(can_take=lambda: True, status=lambda: (True, ''), installs=lambda: (install(),),
                 sampler=lambda: Sampler(reading()), port_ok=lambda slot: zen_port.PortCheck(ok=True),
                 isolation_ok=lambda: True, share_state=lambda: reachable(), entry_ready=lambda sha: False,
                 reachability=Reach, resolve=lambda root, rel, kind: calls.append((rel, kind)), budget=lambda: None)
    state.update(overrides)
    reach = state['reachability']()
    state['reachability'] = lambda: reach
    return reserve.Reserver(reserve.ReserveDeps(**state)), reach


def token_free(reserver):
    """Every Unreal slot is free (the slot pool replaced the token, FR-PC-SLOT-1)."""
    return reserver.slots.free() == tuple(range(SLOT_COUNT))


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
    {'can_take': lambda: False}, {'status': lambda: (False, 'no install')},
    {'installs': lambda: (install('5.8.4-1'),)},
    {'sampler': lambda: Sampler(reading(used=25000))}, {'sampler': lambda: None},
    {'port_ok': lambda slot: zen_port.PortCheck(ok=False, error='x')}, {'isolation_ok': lambda: False},
    {'share_state': lambda: reachable(ok=False)},
    {'resolve': lambda root, rel, kind: (_ for _ in ()).throw(ValueError('outside'))},
    {'reachability': lambda: Reach({'project-fixes': False})},
    {'sampler': lambda: Sampler(reading(), fresh=reading(used=25000))},
    {'sampler': lambda: Sampler(reading(), fresh=None)},
    {'card_keys': lambda: ()},                                   # no Unreal card in use (C-27 b)
])
def test_each_failed_check_declines_and_holds_nothing(override):
    reserver, _reach = make(**override)
    assert reserver.try_reserve(job()) is None
    assert token_free(reserver)


def test_the_reservation_carries_the_unreal_cards_in_use():
    """C-27 b: the Unreal cards in use stay on the reservation; it holds the gated one (render per card D1)."""
    from sethlans_worker_agent.unreal_executor.vram import GpuMem
    keyed = reading(gpus=(GpuMem(0, 'a', 32607, 1000, None, None, 'pci:0000:23:00.0', card_kind=KIND),))
    reserver, _reach = make(card_keys=lambda: ('pci:0000:23:00.0', 'pci:0000:24:00.0'), sampler=lambda: Sampler(keyed))
    reservation = reserver.try_reserve(job())
    assert reservation.card_keys == ('pci:0000:23:00.0', 'pci:0000:24:00.0')
    assert reservation.card_key == 'pci:0000:23:00.0'
    reservation.release()


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


def test_slots_are_taken_lowest_first_and_given_back():
    """FR-PC-SLOT-1: the slot pool replaced the one-token semaphore; check 0 keeps one job at a time here."""
    reserver, _reach = make()
    first, second = reserver.try_reserve(job(job_id=1)), reserver.try_reserve(job(job_id=2))
    assert (first.slot, second.slot) == (0, 1)
    first.release()
    third = reserver.try_reserve(job(job_id=3))
    assert third.slot == 0
    second.release()
    third.release()
    assert token_free(reserver)
    assert reserver.try_reserve(job(job_id=2)) is not None


def test_the_static_gate_runs_before_the_share_and_the_fresh_reading_after_the_probes():
    sampler = Sampler(reading(used=25000))
    reserver, reach = make(sampler=lambda: sampler)
    assert reserver.try_reserve(job()) is None
    assert reach.calls == [] and sampler.fresh_calls == 0


class TestGate:
    """FR-UE-21 worked examples (PoC caps 9,000 + 2,048; 1,000 MB in use), per-machine memory test D7."""

    @pytest.mark.parametrize('total', [32607, 20464, 16303, 12282])
    def test_pilot_has_no_memory_need(self, total):
        """D7, S2: the 12,478 MB pilot minimum is gone; a card of the pilot's kind is enough."""
        result = vram_gate_check.evaluate(params(kind='pilot'), reading(total=total))
        assert result.ok and result.need_mb is None and result.gate == 'pilot'

    def test_pilot_needs_a_reading_that_measures_the_card(self):
        result = vram_gate_check.evaluate(params(kind='pilot'), reading(source='override'))
        assert not result.ok and result.message == "a pilot needs a live GPU memory reading (source override)."

    @pytest.mark.parametrize('total,ok,text', [
        (32607, True, ''), (16303, True, ''), (12282, True, ''), (11264, True, ''),
        (9600, False, "needs 7930 MB (this machine's test); 7928 MB usable on a 9600 MB GPU (1000 MB in use)."),
    ])
    def test_measured_gate(self, total, ok, text):
        """D7: the result peak against floor(total * 0.93) - used, no margin and no pool-caps bound."""
        result = vram_gate_check.evaluate(params(peak=7930), reading(total=total))
        assert result.ok is ok and result.message == text and result.need_mb == 7930

    def test_the_first_card_that_fits_is_gated_and_named_in_the_summary(self):
        """Render per card D4 (D22, FR-GU-10): the largest card first; a busy one gives way to the next that fits."""
        from sethlans_worker_agent.unreal_executor.vram import GpuMem
        two = reading(gpus=(GpuMem(0, 'a', 32607, 1000, None, None, 'pci:0000:01:00.0', card_kind=KIND),
                            GpuMem(1, 'b', 16303, 9000, None, None, 'pci:0000:02:00.0', card_kind=KIND)))
        result = vram_gate_check.evaluate(params(peak=7930), two)
        assert result.ok and result.summary['vram_mb'] == 32607 and result.summary['vram_need_mb'] == 7930
        assert result.card_key == 'pci:0000:01:00.0'
        busy = reading(gpus=(GpuMem(0, 'a', 32607, 30000, None, None, 'pci:0000:01:00.0', card_kind=KIND),
                             GpuMem(1, 'b', 16303, 1000, None, None, 'pci:0000:02:00.0', card_kind=KIND)))
        result = vram_gate_check.evaluate(params(peak=7930), busy)
        assert result.ok and result.card_key == 'pci:0000:02:00.0' and result.summary['vram_mb'] == 16303

    def test_no_reading(self):
        assert not vram_gate_check.evaluate(params(), None).ok
        assert not vram_gate_check.evaluate(params(), SimpleNamespace(gpus=(), source='nvidia-smi')).ok
