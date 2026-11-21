# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The memory gate on the job's one card and its hand-off to the reservation
(GPU memory reader D22, FR-GU-10, AC-21).
"""
import logging
from types import SimpleNamespace

import pytest

from sethlans_worker_agent.unreal_executor import executor, vram_gate_check
from sethlans_worker_agent.unreal_executor.vram import GpuMem
from tests.unit.worker.unreal._executor_fakes import job, params, reading
from tests.unit.worker.unreal.test_reserve import Sampler, make, token_free

IDLE_KEY = 'pci:0000:41:00.0'
BUSY_KEY = 'pci:0000:0a:00.0'
BUSY = GpuMem(0, 'GeForce Test 16G', 16303, 12000, None, None, BUSY_KEY)
IDLE = GpuMem(1, 'Radeon Test 24G', 24560, 300, None, None, IDLE_KEY)
LOGGER = 'sethlans_worker_agent.unreal_executor.reserve'


def two_cards(*gpus):
    return reading(gpus=gpus or (BUSY, IDLE))


class TestJobCard:

    def test_ac21_a_job_that_fits_the_idle_card_passes_on_it(self):
        """The busy card (lower key, listed first) cannot hold the 9,120 MB need; the gate checks the idle one."""
        result = vram_gate_check.evaluate(params(peak=7930), two_cards())
        assert result.ok and result.card_key == IDLE_KEY and result.need_mb == 9120
        assert result.summary['vram_mb'] == 24560 and result.summary['vram_used_mb'] == 300
        assert result.reading.gpus == (BUSY, IDLE)

    def test_ac21_a_pilot_also_gates_the_idle_card(self):
        result = vram_gate_check.evaluate(params(kind='pilot'), two_cards())
        assert result.ok and result.card_key == IDLE_KEY and result.gate == 'pilot'

    def test_ac21_two_equal_cards_gate_the_lower_key(self):
        twin_a = GpuMem(0, 'Radeon Test 20G', 20464, 500, None, None, 'pci:0000:62:00.0')
        twin_b = GpuMem(1, 'Arc Test 20G', 20464, 9000, None, None, 'pci:0000:03:00.0')
        result = vram_gate_check.evaluate(params(peak=7930), two_cards(twin_a, twin_b))
        assert result.card_key == 'pci:0000:03:00.0' and result.summary['vram_used_mb'] == 9000

    def test_equal_cards_compare_keys_as_integers_not_text(self):
        """'pci:00000000:ff:...' sorts first as text; bus 0x01 is the lower address."""
        wide = GpuMem(0, 'Radeon Test 20G', 20464, 500, None, None, 'pci:00000000:ff:00.0')
        narrow = GpuMem(1, 'GeForce Test 20G', 20464, 500, None, None, 'pci:0000:01:00.0')
        assert vram_gate_check.evaluate(params(peak=7930), two_cards(wide, narrow)).card_key == 'pci:0000:01:00.0'

    def test_a_keyless_card_loses_a_tie(self):
        keyless = GpuMem(0, 'Arc Test 20G', 20464, 500, None, None, None)
        keyed = GpuMem(1, 'Radeon Test 20G', 20464, 500, None, None, 'pci:0000:c1:00.0')
        assert vram_gate_check.job_card(two_cards(keyless, keyed)) is keyed

    def test_need_failure_names_the_checked_card(self):
        full = GpuMem(1, 'Radeon Test 24G', 24560, 20000, None, None, IDLE_KEY)
        result = vram_gate_check.evaluate(params(peak=7930), two_cards(BUSY, full))
        assert not result.ok and result.card_key == IDLE_KEY
        assert result.message == 'needs 9120 MB; 2840 MB usable on a 24560 MB GPU (20000 MB in use).'

    def test_caps_failure_names_the_checked_card(self):
        small = GpuMem(3, 'Arc Test 11G', 11264, 0, None, None, 'pci:0000:05:00.0')
        result = vram_gate_check.evaluate(params(peak=7930), two_cards(small))
        assert not result.ok and result.card_key == 'pci:0000:05:00.0' and 'pool caps' in result.message

    @pytest.mark.parametrize('total', [32607, 8192])
    def test_the_override_has_no_card_key(self, total):
        result = vram_gate_check.evaluate(params(peak=7930), reading(total=total, source='override'))
        assert result.card_key is None and result.ok is (total == 32607)

    def test_messages_name_no_vendor_tool(self):
        texts = [vram_gate_check.evaluate(params(kind='pilot'), reading(source='override')).message,
                 vram_gate_check.evaluate(params(peak=7930), None).message]
        assert all('nvidia' not in text.lower() for text in texts)


def _reserver(gpus, keys, **kwargs):
    live = reading(gpus=gpus)
    return make(sampler=lambda: Sampler(live), card_keys=lambda: keys, **kwargs)[0]


class TestGatedCardHandOff:

    def test_the_reservation_keeps_the_gated_card(self):
        reserver = _reserver((BUSY, IDLE), (BUSY_KEY, IDLE_KEY))
        reservation = reserver.try_reserve(job())
        assert reservation.gate.card_key == IDLE_KEY and reservation.card_keys == (BUSY_KEY, IDLE_KEY)
        assert reservation.card_key == IDLE_KEY  # the card job_claim holds (render per card FR-PC-CAP-3)
        reservation.release()

    def test_a_gated_card_no_longer_in_use_skips_the_job_and_frees_the_token(self, caplog):
        reserver = _reserver((BUSY, IDLE), (BUSY_KEY,))
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert reserver.try_reserve(job()) is None
        assert token_free(reserver)
        assert 'the card the memory gate checked is no longer an Unreal card in use' in caplog.text

    @pytest.mark.parametrize('in_use', ['pci:00000000:41:00.0', 'pci:000000:41:00.0'])
    def test_keys_compare_as_address_integers(self, in_use):
        reservation = _reserver((BUSY, IDLE), (in_use,)).try_reserve(job())
        assert reservation is not None and reservation.gate.card_key == IDLE_KEY
        assert reservation.card_key == in_use  # the hold uses the inventory's spelling of that address
        reservation.release()

    @pytest.mark.parametrize('gated,in_use', [('pci:bogus', ('pci:other',)), ('pci:bogus', (IDLE_KEY,)),
                                              (IDLE_KEY, ('pci:other',))])
    def test_unreadable_keys_never_match(self, gated, in_use):
        """Two unreadable keys both read as no address: that must not count as the same card (fail closed)."""
        card = GpuMem(1, 'Radeon Test 24G', 24560, 300, None, None, gated)
        reserver = _reserver((card,), in_use)
        assert reserver.try_reserve(job()) is None and token_free(reserver)

    def test_a_keyless_pilot_card_is_skipped_before_the_render(self, caplog):
        keyless = GpuMem(1, 'Arc Test 24G', 24560, 300, None, None, None)
        reserver = _reserver((keyless,), (IDLE_KEY,))
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert reserver.try_reserve(job(kind='pilot')) is None
        assert token_free(reserver) and 'has no PCI address, so a pilot could not be reported' in caplog.text

    def test_a_keyed_pilot_card_reserves(self):
        reservation = _reserver((BUSY, IDLE), (IDLE_KEY,)).try_reserve(job(kind='pilot'))
        assert reservation is not None and reservation.gate.card_key == IDLE_KEY
        reservation.release()

    def test_the_override_reserves_without_a_card_key(self):
        reserver, _ = make(sampler=lambda: Sampler(reading(source='override')), card_keys=lambda: (IDLE_KEY,))
        reservation = reserver.try_reserve(job())
        assert reservation is not None and reservation.gate.card_key is None
        assert reservation.card_key == IDLE_KEY  # no gated card: the first Unreal card in use is held
        reservation.release()

    def test_the_fresh_gate_card_is_the_one_checked(self):
        """The static gate saw the card in use; the fresh reading's largest card is not: skip."""
        big = GpuMem(2, 'Radeon Test 32G', 32607, 0, None, None, 'pci:0000:83:00.0')
        sampler = Sampler(reading(gpus=(IDLE,)), fresh=reading(gpus=(IDLE, big)))
        reserver, _ = make(sampler=lambda: sampler, card_keys=lambda: (IDLE_KEY,))
        assert reserver.try_reserve(job()) is None and sampler.fresh_calls == 1 and token_free(reserver)


class TestProductionDeps:

    def test_the_production_builder_always_sets_card_keys(self):
        assert executor.default_reserve_deps().card_keys is not None

    @pytest.mark.parametrize('found,keys', [(None, ()), (((), 'no card ticked'), ()),
                                            ((('A', 'B'), ''), (IDLE_KEY, BUSY_KEY))])
    def test_its_card_keys_come_from_the_unreal_cards_in_use(self, monkeypatch, found, keys):
        from sethlans_worker_agent.cards import unreal_cards
        by_name = {'A': SimpleNamespace(key=IDLE_KEY), 'B': SimpleNamespace(key=BUSY_KEY)}
        value = None if found is None else (tuple(by_name[name] for name in found[0]), found[1])
        monkeypatch.setattr(unreal_cards, 'current', lambda: value)
        assert executor.default_reserve_deps().card_keys() == keys
