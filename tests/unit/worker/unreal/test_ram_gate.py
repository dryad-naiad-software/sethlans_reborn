# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The RAM gate, reserver check 9a (render per card D9, FR-PC-RAM-4..6; AC-5, AC-8): never applied to a
job alone; with another job running the physical and the commit check each decline with their
message, a failed or stale figure fails closed, both checks exactly met admit; a just-claimed
Blender job counts ``ram_blender_mb`` for 300 s; a cold slot's need includes the warm-up.
"""
import logging

import pytest

from sethlans_worker_agent.capacity import WorkerCapacity
from sethlans_worker_agent.unreal_executor import cvars, ram_ledger, reserve_local, warmup
from sethlans_worker_agent.unreal_executor.job_ram import RamNeed
from tests.unit.worker.executors._fakes import profile
from tests.unit.worker.unreal._executor_fakes import job, machine_result, params
from tests.unit.worker.unreal.test_ram_need import RAM
from tests.unit.worker.unreal.test_reserve_card import CARDS, IDLE_KEY, KEYS, OTHER_KEY
from tests.unit.worker.unreal.test_reserve import Sampler, make, reading

SETTINGS = reserve_local.RamSettings(ram_reserve_mb=4096, commit_reserve_mb=8192, default_ws_mb=16384,
                                     default_commit_mb=32768, blender_mb=4096)
NEED = RamNeed(ws_mb=16384, commit_mb=40944, source='default', warmup=True,  # 16384 + the 24560 MB card
               render_ws_mb=16384, render_commit_mb=40944)
LOGGER = 'sethlans_worker_agent.unreal_executor.reserve'


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def readers(available=1 << 20, headroom=1 << 20):
    def read(value):
        def reader():
            if isinstance(value, Exception):
                raise value
            return value
        return reader
    return reserve_local.RamReaders(read(available), read(headroom))


def reserver(cap, ram=None, **kwargs):
    live = reading(gpus=CARDS)
    return make(sampler=lambda: Sampler(live), card_keys=lambda: KEYS, capacity=lambda: cap,
                ram=ram or ram_ledger.RamLedger(), **kwargs)[0]


@pytest.fixture
def cap():
    return WorkerCapacity(profile(gpu_slot_count=0))


class TestNeverAlone:

    def test_a_job_alone_passes_whatever_the_free_ram(self, cap):
        r = reserver(cap, ram_readers=readers(0, OSError('not read')))
        reservation = r.try_reserve(job())
        assert reservation is not None and reservation.card_key == IDLE_KEY
        assert reservation.ram_need == NEED   # worked out and registered all the same (FR-PC-RAM-5)
        assert r.ram.snapshot()[reservation.claim_id].need_commit == 40944
        reservation.release()

    def test_beside_another_unreal_job_it_applies(self, cap, caplog):
        cap.reserve_unreal_card(7, OTHER_KEY)
        r = reserver(cap, ram_readers=readers(available=0))
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert r.try_reserve(job()) is None
        assert "needs 16384 MB of physical RAM; 0 MB free above the 4096 MB reserve while other jobs run." \
            in caplog.text
        assert r.slots.free() == tuple(range(16)) and r.ram.snapshot() == {}

    def test_a_ledger_claim_alone_also_makes_it_apply(self, cap):
        """A Blender job just claimed is in the ledger even before its card shows in the capacity."""
        ledger = ram_ledger.RamLedger()
        ledger.register_blender(5)
        assert reserver(cap, ram=ledger, ram_readers=readers(available=0)).try_reserve(job()) is None

    def test_the_second_unreal_claim_counts_the_first_ones_need(self, cap):
        r = reserver(cap, ram_readers=readers(available=4096 + 16384 + 16384, headroom=8192 + 40944 * 2))
        first = r.try_reserve(job(job_id=1))
        cap.reserve_unreal_card(1, first.card_key)
        second = r.try_reserve(job(job_id=2))
        assert second is not None and second.card_key == OTHER_KEY   # its card: 16384 + 16303 = 32687 < 32768
        cap.reserve_unreal_card(2, second.card_key)
        r.ram.publish(first.claim_id, 1, 1)  # not in a launch: still counted at need - current
        assert r.try_reserve(job(job_id=3)) is None   # no free card anyway; and no RAM


class TestChecks:

    def test_the_physical_check_declines_with_its_message(self):
        decision = reserve_local.check(NEED, ram_ledger.RamLedger(), readers(available=4096 + 16383), SETTINGS)
        assert not decision.ok and decision.checked
        assert decision.message == ("needs 16384 MB of physical RAM; 16383 MB free above the 4096 MB reserve "
                                    "while other jobs run.")

    def test_the_commit_check_declines_with_its_message(self):
        decision = reserve_local.check(NEED, ram_ledger.RamLedger(), readers(headroom=8192 + 40943), SETTINGS)
        assert not decision.ok
        assert decision.message == "needs 40944 MB of commit; 40943 MB commit headroom above the 8192 MB reserve."

    def test_both_checks_exactly_met_admit(self):
        ledger = ram_ledger.RamLedger()
        ledger.register('other', 10000, 20000)
        ledger.publish('other', 4000, 5000)                 # outstanding 6000 and 15000
        exact = readers(available=4096 + 6000 + 16384, headroom=8192 + 15000 + 40944)
        assert reserve_local.check(NEED, ledger, exact, SETTINGS).ok
        short = readers(available=4096 + 6000 + 16384 - 1, headroom=8192 + 15000 + 40944)
        assert not reserve_local.check(NEED, ledger, short, SETTINGS).ok
        short = readers(available=4096 + 6000 + 16384, headroom=8192 + 15000 + 40944 - 1)
        assert not reserve_local.check(NEED, ledger, short, SETTINGS).ok


class TestFailClosed:

    def test_a_failed_figure_of_a_running_job(self):
        ledger = ram_ledger.RamLedger()
        ledger.register('other', 1, 1)
        ledger.mark_failed('other')
        decision = reserve_local.check(NEED, ledger, readers(), SETTINGS)
        assert (decision.ok, decision.message) == (False, ram_ledger.UNAVAILABLE_REASON)

    def test_a_stale_figure_of_a_job_in_a_launch(self):
        clock = Clock()
        ledger = ram_ledger.RamLedger(clock)
        ledger.register('other', 1, 1)
        ledger.launch_started('other')
        clock.now += 10.0
        assert reserve_local.check(NEED, ledger, readers(), SETTINGS).ok   # 10 s is not stale yet
        clock.now += 0.5
        assert reserve_local.check(NEED, ledger, readers(), SETTINGS).message == ram_ledger.UNAVAILABLE_REASON

    @pytest.mark.parametrize('which', ['available', 'headroom'])
    def test_a_reader_that_fails(self, which, caplog):
        failing = readers(**{which: OSError('refused')})
        with caplog.at_level(logging.WARNING, logger='sethlans_worker_agent.unreal_executor.reserve_local'):
            decision = reserve_local.check(NEED, ram_ledger.RamLedger(), failing, SETTINGS)
        assert (decision.ok, decision.message) == (False, ram_ledger.UNAVAILABLE_REASON)
        assert 'OSError: refused' in caplog.text

    def test_through_the_reserver(self, cap):
        cap.reserve_unreal_card(7, OTHER_KEY)
        ledger = ram_ledger.RamLedger()
        ledger.register('other', 1, 1)
        ledger.mark_failed('other')
        r = reserver(cap, ram=ledger, ram_readers=readers())
        assert r.try_reserve(job()) is None and r.slots.free() == tuple(range(16))

    def test_a_gated_job_without_its_card_size_is_declined(self):
        p = params()
        gate = type('Gate', (), {'summary': {}})()
        with pytest.raises(ValueError, match='size of its graphics card'):
            reserve_local.card_total_mb(p, gate)
        assert reserve_local.card_total_mb(params(kind='shots', frame=0), gate) == 0


class TestBlenderWindow:

    def test_a_blender_claim_counts_for_300_seconds(self):
        clock = Clock()
        ledger = ram_ledger.RamLedger(clock)
        ledger.register_blender(5)
        fits = readers(available=4096 + 16384, headroom=1 << 20)
        assert not reserve_local.check(NEED, ledger, fits, SETTINGS).ok   # 4096 MB still to come
        clock.now += 300.0
        assert not reserve_local.check(NEED, ledger, fits, SETTINGS).ok
        clock.now += 0.1
        assert reserve_local.check(NEED, ledger, fits, SETTINGS).ok
        assert ledger.has_claims()   # still running: dropped only at its release
        ledger.unregister_blender(5)
        assert not ledger.has_claims()

    def test_the_blender_side_fits_or_says_why(self):
        ledger = ram_ledger.RamLedger()
        ledger.register('unreal', 16384, 40944)
        own = 4096   # the Blender job's own memory (ram_blender_mb)
        assert reserve_local.blender_fits(ledger, readers(4096 + 16384 + own, 8192 + 40944 + own), SETTINGS) == ''
        assert reserve_local.blender_fits(ledger, readers(4096 + 16384 + own - 1, 1 << 20), SETTINGS) == (
            "needs 4096 MB of physical RAM beside 16384 MB the running jobs may still take; 20479 MB free above "
            "the 4096 MB reserve.")
        assert reserve_local.blender_fits(ledger, readers(1 << 20, 8192 + 40944 + own - 1), SETTINGS) == (
            "needs 4096 MB of commit beside 40944 MB the running jobs may still take; 45039 MB commit headroom "
            "above the 8192 MB reserve.")
        ledger.mark_failed('unreal')
        assert reserve_local.blender_fits(ledger, readers(), SETTINGS) == ram_ledger.UNAVAILABLE_REASON


class TestColdSlot:

    def test_a_slot_1_claim_plans_the_warm_up_slot_0_has_done(self, cap, tmp_path):
        """AC-5: slot 0's marker does not make slot 1 warm; slot 1's need includes the warm-up figure."""
        p = params()
        marker = warmup.marker_path(tmp_path, p.overlay_sha256, p.engine_build, 0)
        warmup.record_success(marker, p.map, p.sequence, cvars.warmup_cvars_sha256(p.merged))
        r = reserver(cap, ram_readers=readers(), entry_dir=lambda sha: tmp_path)
        first = r.try_reserve(job(job_id=1, unreal_machine_results=[machine_result(ram=RAM)]))
        cap.reserve_unreal_card(1, first.card_key)
        second = r.try_reserve(job(job_id=2, unreal_machine_results=[machine_result(ram=RAM)]))
        assert (first.slot, second.slot) == (0, 1)
        assert (first.ram_need.ws_mb, first.ram_need.commit_mb, first.ram_need.warmup) == (7500, 17600, False)
        assert (second.ram_need.ws_mb, second.ram_need.commit_mb, second.ram_need.warmup) == (12000, 30000, True)
        assert r.ram.snapshot()[second.claim_id].need_ws == 12000
