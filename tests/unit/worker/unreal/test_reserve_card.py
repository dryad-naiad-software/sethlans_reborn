# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The card an Unreal claim holds (render per card D1, D3, D4, D14; FR-PC-CAP-3, FR-PC-SEL-1, -5; AC-3).

A gated job takes the first free card, in D8 order, that it fits; a held card
is skipped; with no free card that fits it is declined in ``try_reserve``, so
the claim walk moves on to the jobs behind it; ``shots`` take the first free
card; the reading override holds D8's card and runs one Unreal job at a time;
the audio job's card is given to no other job while it is held.
"""
import logging
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import executors, job_selection
from sethlans_worker_agent.capacity import WorkerCapacity
from sethlans_worker_agent.unreal_executor import reserve_card, vram_gate_check
from sethlans_worker_agent.unreal_executor.vram import GpuMem
from tests.unit.worker.executors._fakes import blender_job, install_capacity, key, profile
from tests.unit.worker.unreal._executor_fakes import KIND, job, params, reading
from tests.unit.worker.unreal.sequence._pieces import audio_data
from tests.unit.worker.unreal.test_reserve import Sampler, make, token_free

IDLE_KEY = 'pci:0000:41:00.0'   # D8's card (the larger one)
OTHER_KEY = 'pci:0000:0a:00.0'
CARDS = (GpuMem(0, 'Test 16G', 16303, 300, None, None, OTHER_KEY, card_kind=KIND),
         GpuMem(1, 'Test 24G', 24560, 300, None, None, IDLE_KEY, card_kind=KIND))
KEYS = (OTHER_KEY, IDLE_KEY)
LOGGER = 'sethlans_worker_agent.unreal_executor.reserve'


def _reserver(cap, keys=KEYS, **kwargs):
    live = reading(gpus=CARDS)
    return make(sampler=lambda: Sampler(live), card_keys=lambda: keys, capacity=lambda: cap, **kwargs)[0]


class TestGatedJob:

    def test_holds_the_gated_card_when_it_is_free(self):
        cap = WorkerCapacity(profile(gpu_slot_count=0))
        reservation = _reserver(cap).try_reserve(job())
        assert reservation.card_key == IDLE_KEY
        reservation.release()

    @pytest.mark.parametrize('holder', ['UNREAL', 'BLENDER'])
    def test_a_held_card_is_skipped_for_the_next_free_card(self, holder):
        """D4: D8's card is held (by address, in any spelling), so the job takes the next free card it fits."""
        cap = WorkerCapacity(profile(gpu_slot_count=0))
        if holder == 'UNREAL':
            cap.reserve_unreal_card(1, 'pci:00000000:41:00.0')  # the same address, another spelling
        else:
            cap._gpu_assignments[IDLE_KEY] = 1  # a Blender job on that card
        reservation = _reserver(cap).try_reserve(job())
        assert reservation.gate.card_key == OTHER_KEY and reservation.card_key == OTHER_KEY
        assert reservation.gate.summary['vram_mb'] == 16303  # the summary names the card it takes
        reservation.release()

    def test_the_first_free_card_that_fits_not_the_first_free_card(self):
        """D4: the larger free card is too busy for the job; the next free card in D8 order that fits is taken."""
        cap = WorkerCapacity(profile(gpu_slot_count=0))
        busy_big = GpuMem(1, 'Test 24G', 24560, 20000, None, None, IDLE_KEY, card_kind=KIND)
        live = reading(gpus=(CARDS[0], busy_big))
        reservation = make(sampler=lambda: Sampler(live), card_keys=lambda: KEYS, capacity=lambda: cap)[0] \
            .try_reserve(job())
        assert reservation.card_key == OTHER_KEY
        reservation.release()

    def test_no_free_card_that_fits_declines_and_names_the_first_candidate(self, caplog):
        cap = WorkerCapacity(profile(gpu_slot_count=0))
        cap.reserve_unreal_card(1, IDLE_KEY)
        small_busy = GpuMem(0, 'Test 16G', 16303, 15000, None, None, OTHER_KEY, card_kind=KIND)
        reserver = make(sampler=lambda: Sampler(reading(gpus=(small_busy, CARDS[1]))), card_keys=lambda: KEYS,
                        capacity=lambda: cap)[0]
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert reserver.try_reserve(job()) is None
        assert token_free(reserver) and 'on a 16303 MB GPU (15000 MB in use)' in caplog.text

    def test_no_free_card_declines_and_frees_the_slot(self, caplog):
        cap = WorkerCapacity(profile(gpu_slot_count=0))
        cap.reserve_unreal_card(1, OTHER_KEY)
        cap._gpu_assignments[IDLE_KEY] = 2  # a Blender job on the other card
        reserver = _reserver(cap)
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert reserver.try_reserve(job()) is None
        assert token_free(reserver) and vram_gate_check.NO_FREE_CARD in caplog.text

    def test_a_card_taken_after_the_fresh_gate_still_declines(self, caplog):
        """The hold check after the gate stays: a card taken since the free cards were read is not held twice."""
        cap = WorkerCapacity(profile(gpu_slot_count=0))
        gate = SimpleNamespace(card_key=IDLE_KEY)
        cap.reserve_unreal_card(1, IDLE_KEY)
        deps = SimpleNamespace(capacity=lambda: cap, d8_key=None)
        key, why = reserve_card.choose(params(), gate, KEYS, deps)
        assert key is None and 'is held by another job' in why

    def test_a_blender_job_behind_a_declined_unreal_job_is_claimed(self, monkeypatch):
        """Concurrency review B2: a hold refused at claim would stall the walk on the same Unreal job."""
        cap = install_capacity(gpu_slot_count=1, cpu_slot_count=0)
        cap.reserve_unreal_card(1, IDLE_KEY)
        cap.reserve_unreal_card(4, OTHER_KEY)  # every Unreal card is held: the Unreal job is declined
        unreal = SimpleNamespace(renderer='UNREAL', capacity_mode='unreal_card', try_reserve=_reserver(cap).try_reserve)
        real = executors.get_executor
        monkeypatch.setattr(executors, 'get_executor', lambda renderer: unreal if renderer == 'UNREAL' else real(renderer))
        jobs = [job(job_id=2), blender_job(3)]
        selection = job_selection.select_claimable_job(jobs, cap)
        assert selection is not None and selection.job is jobs[1]


class TestShots:

    def test_takes_the_first_free_card(self):
        cap = WorkerCapacity(profile(gpu_slot_count=0))
        cap.reserve_unreal_card(1, OTHER_KEY)
        reservation = _reserver(cap).try_reserve(job(kind='shots', frame=0))
        assert reservation.card_key == IDLE_KEY
        reservation.release()

    def test_no_free_card_declines(self):
        cap = WorkerCapacity(profile(gpu_slot_count=0))
        cap.reserve_unreal_card(1, OTHER_KEY)
        cap.reserve_unreal_card(2, IDLE_KEY)
        reserver = _reserver(cap)
        assert reserver.try_reserve(job(kind='shots', frame=0)) is None and token_free(reserver)


def _choose(cap, d8=IDLE_KEY):
    deps = SimpleNamespace(capacity=lambda: cap, d8_key=lambda: d8)
    return reserve_card.choose(params(), SimpleNamespace(card_key=None), KEYS, deps)


class TestReadingOverride:
    """D14: no card key in the reading; one Unreal job at a time, on D8's card, which the launch is asked for."""

    def test_holds_d8s_card_not_the_first_card(self, caplog):
        cap = WorkerCapacity(profile(gpu_slot_count=0))
        with caplog.at_level(logging.INFO, logger='sethlans_worker_agent.unreal_executor.reserve_card'):
            assert _choose(cap) == (IDLE_KEY, '')
            assert _choose(cap) == (IDLE_KEY, '')
        assert caplog.text.count('one Unreal job at a time') == 1

    def test_one_unreal_job_at_a_time_even_with_a_free_card(self):
        cap = WorkerCapacity(profile(gpu_slot_count=0))
        cap.reserve_unreal_card(1, OTHER_KEY)
        assert _choose(cap) == (None, reserve_card.OVERRIDE_ONE_JOB)

    def test_d8s_card_held_by_blender_or_not_found_declines(self):
        cap = WorkerCapacity(profile(gpu_slot_count=0))
        cap._gpu_assignments[IDLE_KEY] = 9
        assert _choose(cap)[0] is None
        assert _choose(WorkerCapacity(profile(gpu_slot_count=0)), d8=None) == (None, reserve_card.NO_D8_CARD)

    def test_the_poll_asks_for_no_second_unreal_job(self, monkeypatch, caplog):
        """FR-PC-SEL-5: with the override set, ``can_take_unreal`` is false while an Unreal job holds a card."""
        from sethlans_worker_agent import config_unreal, unreal_bridge
        from sethlans_worker_agent.cards import unreal_cards
        monkeypatch.setattr(config_unreal, 'VRAM_MB', 24000)
        monkeypatch.setattr(unreal_cards, 'current_keys', lambda cached_only=True: KEYS)
        cap = install_capacity(gpu_slot_count=0, cpu_slot_count=0)
        with caplog.at_level(logging.INFO, logger=reserve_card.__name__):
            assert unreal_bridge.can_take_unreal() is True
            cap.reserve_unreal_card(1, OTHER_KEY)
            assert unreal_bridge.can_take_unreal() is False  # IDLE_KEY is free, but one Unreal job runs
            monkeypatch.setattr(config_unreal, 'VRAM_MB', 0)
            assert unreal_bridge.can_take_unreal() is True  # without the override the free card is asked for
        assert caplog.text.count('one Unreal job at a time') == 1

    def test_through_the_reserver_the_held_card_is_d8s(self):
        cap = WorkerCapacity(profile(gpu_slot_count=0))
        reserver, _ = make(sampler=lambda: Sampler(reading(source='override')), card_keys=lambda: KEYS,
                           capacity=lambda: cap, d8_key=lambda: IDLE_KEY)
        reservation = reserver.try_reserve(job())
        assert reservation.gate.card_key is None and reservation.card_key == IDLE_KEY
        reservation.release()


class TestAudioCardToItself:
    """AC-3 / Q6: an audio job's card is given to no other job until its release."""

    def test_no_other_unreal_or_blender_job_takes_the_audio_card(self):
        cap = install_capacity(gpu_slot_count=2, cpu_slot_count=0)  # Blender cards key(0), key(1)
        keys = (key(0), key(1))
        gpus = tuple(GpuMem(i, f'Test {i}', 24560 if i == 0 else 16303, 300, None, None, k, card_kind=KIND)
                     for i, k in enumerate(keys))
        live = reading(gpus=gpus)
        reserver = make(sampler=lambda: Sampler(live), card_keys=lambda: keys, capacity=lambda: cap)[0]
        audio = reserver.try_reserve(dict(audio_data(), id=50))
        assert audio is not None and audio.card_key == key(0)
        assert cap.reserve_unreal_card(50, audio.card_key) is not None
        other = reserver.try_reserve(job(job_id=51))
        assert other.card_key == key(1)  # the next Unreal job skips the audio card (D4)
        other.release()
        assert cap.reserve_for_job(52, 'GPU').card_keys == (key(1),)  # Blender takes only the other card
        assert reserver.try_reserve(job(job_id=54)) is None  # no free card is left for Unreal either
        assert cap.reserve_for_job(53, 'GPU') is None
        cap.release_slot(50)
        audio.release()
        assert cap.reserve_for_job(53, 'GPU').card_keys == (key(0),)
