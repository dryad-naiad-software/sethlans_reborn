# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The memory gate on this machine's own results (Unreal per-machine memory test D7, D10, D11, FR-MT-G-2,
FR-MT-G-3, FR-MT-V-2; AC-3, AC-9, AC-10).

A render or audio job: the free cards in D8 order whose kind has a result; a card passes when the result peak
is at most ``floor(total * 0.93) - used`` (no margin), a card of a kind without a result is skipped. A pilot:
the first free card of its ``unreal.card_kind``, no memory need. The ``GateResult`` carries the card's kind,
its ``used`` and the result; the RAM need comes from that same result.
"""
import pytest

from sethlans_worker_agent.capacity import WorkerCapacity
from sethlans_worker_agent.unreal_executor import ram_ledger, reserve_local, vram_gate_check
from sethlans_worker_agent.unreal_executor.vram import GpuMem
from tests.unit.worker.executors._fakes import profile
from tests.unit.worker.unreal._executor_fakes import KIND, job, machine_result, params, reading
from tests.unit.worker.unreal.sequence._pieces import audio_params
from tests.unit.worker.unreal.test_ram_gate import readers
from tests.unit.worker.unreal.test_ram_need import RAM
from tests.unit.worker.unreal.test_reserve import Sampler, make

OTHER = '1002:744c:00c8'
BIG_KEY, SMALL_KEY = 'pci:0000:41:00.0', 'pci:0000:0a:00.0'
LINE = 30324                      # floor(32607 * 0.93)


def card(key, total, used, kind, index=0):
    return GpuMem(index, f'Test {total}', total, used, None, None, key, card_kind=kind)


def results(*entries):
    return {'unreal_machine_results': [machine_result(peak=peak, kind=kind, measurement_id=mid)
                                       for mid, (kind, peak) in enumerate(entries, start=1)]}


class TestTheLine:
    """AC-3: a result peak equal to ``floor(total * 0.93) - used`` is admitted; one MB more is declined."""

    @pytest.mark.parametrize('used', [0, 1000, 17123])
    def test_exactly_at_the_line_passes(self, used):
        result = vram_gate_check.evaluate(params(peak=LINE - used), reading(used=used))
        assert result.ok and result.need_mb == LINE - used and result.message == ''

    @pytest.mark.parametrize('used', [0, 1000, 17123])
    def test_one_mb_over_fails_with_the_text(self, used):
        result = vram_gate_check.evaluate(params(peak=LINE - used + 1), reading(used=used))
        assert not result.ok and result.need_mb == LINE - used + 1
        assert result.message == (f"needs {LINE - used + 1} MB (this machine's test); {LINE - used} MB usable on a "
                                  f"32607 MB GPU ({used} MB in use).")

    def test_the_audio_job_uses_the_same_line(self):
        """D10, AC-9: the sound job's gate is the piece key's result peak."""
        assert vram_gate_check.evaluate(audio_params(unreal_machine_results=[machine_result(LINE - 1000)]),
                                        reading()).ok
        over = vram_gate_check.evaluate(audio_params(unreal_machine_results=[machine_result(LINE - 999)]), reading())
        assert not over.ok and over.gate == 'audio' and over.message.startswith("needs 29325 MB (this machine's test)")


class TestKinds:

    def test_a_card_whose_kind_has_no_result_is_skipped(self):
        """The larger card (first in D8 order) is of a kind this machine has no result for."""
        two = reading(gpus=(card(BIG_KEY, 32607, 0, OTHER, 0), card(SMALL_KEY, 16303, 300, KIND, 1)))
        result = vram_gate_check.evaluate(params(peak=7930), two)
        assert result.ok and result.card_key == SMALL_KEY and result.card_kind == KIND
        assert result.summary['vram_mb'] == 16303 and result.result.card_kind == KIND

    @pytest.mark.parametrize('kinds', [(OTHER,), (None,), (OTHER, None)])
    def test_no_card_with_a_result_fails_with_the_text(self, kinds):
        gpus = tuple(card(f'pci:0000:0{i + 1}:00.0', 32607, 0, kind, i) for i, kind in enumerate(kinds))
        result = vram_gate_check.evaluate(params(peak=1000), reading(gpus=gpus))
        assert (result.ok, result.message) == (False, "no free card with a memory result for this render.")
        assert result.card_key is None and result.result is None and result.need_mb is None

    def test_an_empty_result_list_finds_no_card(self):
        result = vram_gate_check.evaluate(params(unreal_machine_results=[]), reading())
        assert not result.ok and result.message == vram_gate_check.NO_RESULT_CARD

    def test_each_card_is_checked_with_its_own_kinds_result(self):
        """The big card's kind needs more than it has; the small card's kind has a smaller result that fits."""
        two = reading(gpus=(card(BIG_KEY, 32607, 1000, OTHER, 0), card(SMALL_KEY, 16303, 300, KIND, 1)))
        result = vram_gate_check.evaluate(params(**results((OTHER, 29325), (KIND, 14861))), two)
        assert result.ok and result.card_key == SMALL_KEY and result.need_mb == 14861
        assert result.result.measurement_id == 2 and result.summary['vram_measurement_id'] == 2
        assert result.summary['vram_card_kind'] == KIND and result.summary['vram_need_mb'] == 14861

    def test_the_failure_names_the_first_card_with_a_result_not_a_skipped_one(self):
        three = reading(gpus=(card('pci:0000:01:00.0', 32607, 0, None, 0), card(BIG_KEY, 24560, 20000, KIND, 1),
                              card(SMALL_KEY, 16303, 15000, OTHER, 2)))
        result = vram_gate_check.evaluate(params(**results((KIND, 7930), (OTHER, 1500))), three)
        assert not result.ok and result.card_key == BIG_KEY and result.card_kind == KIND
        assert result.message == "needs 7930 MB (this machine's test); 2840 MB usable on a 24560 MB GPU (20000 MB in use)."
        assert (result.used_mb, result.result.measurement_id) == (20000, 1)


class TestGateResultFields:

    def test_a_pass_carries_kind_used_and_result(self):
        result = vram_gate_check.evaluate(params(peak=7930), reading(used=4321))
        assert (result.card_kind, result.used_mb, result.result.peak_mb) == (KIND, 4321, 7930)
        assert result.result.measurement_id == 1 and result.gate == 'measured'
        assert result.summary['vram_used_mb'] == 4321 and result.summary['vram_usable_mb'] == LINE - 4321
        assert 'vram_measured_peak_mb' not in result.summary

    def test_a_pilot_pass_has_no_need_and_no_result(self):
        result = vram_gate_check.evaluate(params(kind='pilot'), reading(used=777))
        assert (result.ok, result.need_mb, result.result) == (True, None, None)
        assert (result.card_kind, result.used_mb) == (KIND, 777)
        assert result.summary['vram_need_mb'] == '' and 'vram_measurement_id' not in result.summary
        assert result.summary['vram_card_kind'] == KIND


class TestPilotCard:

    def test_only_a_card_of_the_pilots_kind(self):
        """FR-MT-V-2: the larger card (first in D8 order) is of another kind; the pilot takes the smaller one."""
        two = reading(gpus=(card(BIG_KEY, 32607, 0, OTHER, 0), card(SMALL_KEY, 16303, 300, KIND, 1)))
        result = vram_gate_check.evaluate(params(kind='pilot'), two)
        assert result.ok and result.gate == 'pilot' and result.card_key == SMALL_KEY and result.card_kind == KIND

    def test_the_first_card_of_its_kind_in_d8_order(self):
        two = reading(gpus=(card(SMALL_KEY, 16303, 300, KIND, 0), card(BIG_KEY, 24560, 300, KIND, 1)))
        assert vram_gate_check.evaluate(params(kind='pilot'), two).card_key == BIG_KEY

    @pytest.mark.parametrize('kinds', [(OTHER,), (None,), (OTHER, None)])
    def test_no_card_of_its_kind_fails_with_the_text(self, kinds):
        gpus = tuple(card(f'pci:0000:0{i + 1}:00.0', 32607, 0, kind, i) for i, kind in enumerate(kinds))
        result = vram_gate_check.evaluate(params(kind='pilot'), reading(gpus=gpus))
        assert (result.ok, result.message, result.card_key) == (False, f"no free card of kind {KIND}.", None)

    def test_a_pilot_ignores_free_memory(self):
        """AC-3, S2: a card with 2,000 MB free admits a pilot; a full card too (it reports does_not_fit)."""
        assert vram_gate_check.evaluate(params(kind='pilot'), reading(total=16303, used=14303)).ok
        assert vram_gate_check.evaluate(params(kind='pilot'), reading(total=16303, used=16303)).ok

    def test_a_held_card_of_its_kind_is_no_candidate(self):
        two = reading(gpus=(card(BIG_KEY, 32607, 0, KIND, 0), card(SMALL_KEY, 16303, 0, OTHER, 1)))
        result = vram_gate_check.evaluate(params(kind='pilot'), two, free_keys=(SMALL_KEY,))
        assert not result.ok and result.message == f"no free card of kind {KIND}."


class TestMeasuredRam:
    """D11, FR-MT-G-3: the RAM figures are those of the result the gate chose."""

    def test_measured_ram_reads_the_gates_result(self):
        p = params(unreal_machine_results=[machine_result(ram=RAM)])
        gate = vram_gate_check.evaluate(p, reading())
        assert reserve_local.measured_ram(gate) is gate.result.ram and gate.result.ram.prep.ws_mb == 4000
        assert reserve_local.measured_ram(vram_gate_check.evaluate(params(), reading())) is None  # no figures
        assert reserve_local.measured_ram(vram_gate_check.evaluate(params(kind='pilot'), reading())) is None
        assert reserve_local.measured_ram(object()) is None  # a gate without a result attribute


RAM_B = {'prep': {'ws_mb': 3000, 'commit_mb': 9000}, 'warmup': {'ws_mb': 20000, 'commit_mb': 45000, 'cold': True},
         'render': {'ws_mb': 9000, 'commit_mb': 25000}}


def _reserver(big_used):
    gpus = (card(SMALL_KEY, 16303, 300, OTHER, 0), card(BIG_KEY, 24560, big_used, KIND, 1))
    cap = WorkerCapacity(profile(gpu_slot_count=0))
    return make(sampler=lambda: Sampler(reading(gpus=gpus)), card_keys=lambda: (SMALL_KEY, BIG_KEY),
                capacity=lambda: cap, ram=ram_ledger.RamLedger(), ram_readers=readers())[0]


def _two_kinds(job_id=1):
    return job(job_id=job_id, unreal_machine_results=[machine_result(kind=KIND, measurement_id=1, ram=RAM),
                                                      machine_result(kind=OTHER, measurement_id=2, ram=RAM_B)])


class TestRamNeedPerKind:

    def test_the_big_cards_kind_gives_its_figures(self):
        """A warm-up is planned (no cache entry): the larger of prep, cold warm-up and render of that result."""
        reservation = _reserver(big_used=300).try_reserve(_two_kinds())
        assert reservation.card_key == BIG_KEY and reservation.gate.result.measurement_id == 1
        need = reservation.ram_need
        assert (need.ws_mb, need.commit_mb, need.source, need.warmup) == (12000, 30000, 'measured', True)
        reservation.release()

    def test_the_other_cards_kind_gives_the_other_figures(self):
        """The big card is too busy for its result; the small card's kind and its RAM figures are used."""
        reservation = _reserver(big_used=20000).try_reserve(_two_kinds())
        assert reservation.card_key == SMALL_KEY and reservation.gate.result.card_kind == OTHER
        need = reservation.ram_need
        assert (need.ws_mb, need.commit_mb, need.source) == (20000, 45000, 'measured')
        reservation.release()

    def test_a_pilot_uses_the_defaults(self):
        reservation = _reserver(big_used=300).try_reserve(job(kind='pilot'))
        assert reservation.card_key == BIG_KEY and reservation.gate.result is None
        need = reservation.ram_need
        assert (need.ws_mb, need.commit_mb, need.source) == (16384, 16384 + 24560, 'default')
        reservation.release()

    def test_a_result_without_figures_uses_the_defaults(self):
        reservation = _reserver(big_used=300).try_reserve(job())
        assert reservation.gate.result is not None and reservation.gate.result.ram is None
        assert reservation.ram_need.source == 'default'
        reservation.release()
