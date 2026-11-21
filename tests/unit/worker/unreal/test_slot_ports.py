# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""A slot's Zen port belongs to its own zenserver only, and one Unreal job at a time through the real
capacity check (render per card FR-PC-SLOT-1, FR-PC-SLOT-3; chunk 2 review fixes)."""
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import job_processor
from sethlans_worker_agent.capacity import WorkerCapacity
from sethlans_worker_agent.unreal_executor import executor, zen_port, zen_private
from tests.unit.worker.executors._fakes import profile
from tests.unit.worker.unreal._executor_fakes import job
from tests.unit.worker.unreal.test_reserve import make
from tests.unit.worker.unreal.test_zen_private import FakeProc, owners  # noqa: F401 - fixture

CACHE = r'D:\uecache'
SLOT1_PORT = 8669


def _listen(holder_slot):
    """One loopback listener on slot 1's port, held by ``holder_slot``'s farm zenserver (pid 100)."""
    procs = {100: FakeProc(['zenserver.exe', '--data-dir', zen_private.zen_data_dir(CACHE, holder_slot)], pid=100)}
    conn = SimpleNamespace(laddr=SimpleNamespace(ip='127.0.0.1', port=SLOT1_PORT), status='LISTEN', pid=100)
    return dict(net_connections=lambda kind: [conn], process_factory=procs.__getitem__)


class TestSlotPortHolder:

    def test_the_slots_own_server_may_hold_its_port(self, owners):  # noqa: F811
        result = zen_port.check_port(SLOT1_PORT, CACHE, slot=1, **_listen(1))
        assert result.ok and len(result.farm) == 1 and result.foreign == ()

    @pytest.mark.parametrize('holder', [0, 2, 15])
    def test_another_slots_server_is_a_foreign_holder(self, owners, holder):  # noqa: F811
        """UE would log 'different data directory' and shut the sibling's server down: refuse the launch."""
        result = zen_port.check_port(SLOT1_PORT, CACHE, slot=1, **_listen(holder))
        assert not result.ok and len(result.foreign) == 1 and result.farm == ()

    @pytest.mark.parametrize('holder, seen', [(1, True), (2, False)])
    def test_the_loopback_watch_sees_only_the_slots_own_server(self, owners, holder, seen):  # noqa: F811
        assert zen_port.check_loopback(SLOT1_PORT, CACHE, slot=1, **_listen(holder)).seen is seen

    def test_the_watch_passes_its_slot(self):
        calls = []
        watch = zen_port.LoopbackWatch(SLOT1_PORT, CACHE, slot=1, check=lambda *a, **k: (
            calls.append((a, k['slot'])), zen_port.LoopbackCheck(seen=False, ok=True))[1])
        assert watch() is None and calls == [((SLOT1_PORT, CACHE), 1)]

    @pytest.mark.parametrize('n', [0, 1, 15])
    def test_is_farm_zenserver_for_one_slot(self, owners, n):  # noqa: F811
        proc = FakeProc(['zenserver.exe', '--data-dir', zen_private.zen_data_dir(CACHE, n)])
        assert zen_private.is_farm_zenserver(proc, CACHE, slot=n)
        assert zen_private.is_farm_zenserver(proc, CACHE)  # the orphan sweep: any slot
        assert not zen_private.is_farm_zenserver(proc, CACHE, slot=(n + 1) % 16)


def test_a_second_reservation_is_refused_while_the_first_job_holds_the_machine(monkeypatch):
    """Check 0 through the real ``executor._capacity_idle`` keeps one Unreal job at a time in chunk 2."""
    cap = WorkerCapacity(profile(gpu_slot_count=2, cpu_slot_count=1))
    monkeypatch.setattr(job_processor, '_capacity', cap)
    reserver, _reach = make(is_idle=executor._capacity_idle)
    first = reserver.try_reserve(job(job_id=1))
    assert first.slot == 0
    assert cap.reserve_exclusive(1) is not None  # what job_claim does next
    assert reserver.try_reserve(job(job_id=2)) is None
    assert reserver.slots.free() == tuple(range(1, 16))
    cap.release_slot(1)
    first.release()
    second = reserver.try_reserve(job(job_id=2))
    assert second is not None and second.slot == 0
    second.release()
