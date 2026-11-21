# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-process card memory (render per card FR-PC-ATT-1): ``GpuMemoryReading.per_pid`` from the PDH
process items and from ``nvidia-smi -q -d PIDS`` fixtures. Fakes only; runs on every OS.
"""
import pytest

from sethlans_worker_agent.gpu_memory import nvidia_smi, win_backend
from sethlans_worker_agent.gpu_memory.types import GpuMemoryReading
from sethlans_worker_agent.gpu_memory.win_pdh import PDH_CSTATUS_NO_INSTANCE, PDH_CSTATUS_VALID_DATA
from tests.unit.worker.gpu_memory._nvidia_fixtures import BUS_A, BUS_B, BUSY, KEY_A, KEY_B, gpu_block, listing
from tests.unit.worker.gpu_memory._win_fakes import (
    KEY_A as WIN_KEY_A, KEY_B as WIN_KEY_B, LUID_A, LUID_B, MIB, OTHER_PID, OURS, FakeQuery, adapter_item, collect,
    make_backend, os_card, process_item)

ADAPTERS = [adapter_item(LUID_A, 3000), adapter_item(LUID_B, 1000)]


def pdh_sample(processes, pids=OURS):
    backend = make_backend(FakeQuery([collect(ADAPTERS, processes)]))
    assert backend.open() is True
    return backend.sample(pids)


class TestPdh:

    def test_each_pid_on_each_card_in_mib(self):
        reading = pdh_sample([process_item(4242, LUID_A, 1200), process_item(4343, LUID_A, 300),
                              process_item(4343, LUID_B, 40), process_item(OTHER_PID, LUID_A, 900)])
        assert reading.per_pid == {WIN_KEY_A: {4242: 1200, 4343: 300}, WIN_KEY_B: {4343: 40}}
        assert [gpu.sethlans_used_mb for gpu in reading.gpus] == [1500, 40]

    def test_a_pids_phys_items_are_summed_before_flooring(self):
        items = [(process_item(4242, LUID_A, 0, phys=n)[0], MIB // 2, PDH_CSTATUS_VALID_DATA) for n in (0, 1, 2)]
        reading = pdh_sample(items)
        assert reading.per_pid[WIN_KEY_A] == {4242: 1}   # 1.5 MiB in three halves, floored once

    def test_cards_none_of_the_pids_use_have_an_empty_map(self):
        reading = pdh_sample([process_item(OTHER_PID, LUID_A, 900)])
        assert reading.per_pid == {WIN_KEY_A: {}, WIN_KEY_B: {}}
        assert reading.measures_process is True

    def test_only_the_given_pids_are_kept(self):
        reading = pdh_sample([process_item(4242, LUID_A, 100), process_item(4343, LUID_A, 200)],
                             pids=frozenset({4343}))
        assert reading.per_pid[WIN_KEY_A] == {4343: 200}

    def test_invalid_items_are_skipped(self):
        reading = pdh_sample([process_item(4242, LUID_A, 100, status=PDH_CSTATUS_NO_INSTANCE),
                              ('pid_x_luid_0x00000000_0x0001A2B3_phys_0', MIB, PDH_CSTATUS_VALID_DATA)])
        assert reading.per_pid == {WIN_KEY_A: {}, WIN_KEY_B: {}}

    def test_no_process_array_gives_none(self):
        reading = pdh_sample(None)
        assert reading.per_pid is None and reading.measures_process is False

    def test_process_usage_is_the_sum_of_the_per_pid_map(self):
        items = [process_item(4242, LUID_A, 10), process_item(4343, LUID_A, 20), process_item(OTHER_PID, LUID_B, 5)]
        by_pid = win_backend.process_usage_by_pid(items, OURS)
        assert win_backend.process_usage(items, OURS) == win_backend.luid_sums(by_pid) == {
            win_backend.luid_value(LUID_A): 30 * MIB}

    def test_the_cross_check_reading_has_no_map(self):
        cards = (win_backend.Card(os_card(0, WIN_KEY_A), 1),)
        assert win_backend.build_reading(cards, {1: MIB}, None, 1.0).per_pid is None


def smi_run(pids_out):
    def run(args):
        return pids_out if args[1] == nvidia_smi.QUERY_PIDS[1] else None
    return run


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(nvidia_smi, '_is_windows', lambda: False)


class TestNvidiaSmi:

    def test_each_listed_pid_of_ours_per_card(self, linux):
        per = nvidia_smi.attribute_pids([KEY_A, KEY_B], frozenset({4100, 4300}), run=smi_run(BUSY))
        assert per == {KEY_A: {4100: 600}, KEY_B: {4300: 50}}
        assert nvidia_smi.sums([KEY_A, KEY_B], per) == {KEY_A: 600, KEY_B: 50}

    def test_a_card_with_none_of_ours_has_an_empty_map(self, linux):
        per = nvidia_smi.attribute_pids([KEY_A, KEY_B], frozenset({4300}), run=smi_run(BUSY))
        assert per == {KEY_A: {}, KEY_B: {4300: 50}}

    def test_an_unattributable_card_is_left_out(self, linux):
        out = listing(gpu_block(BUS_A, (4100, 'G', 'render-app', 'N/A')), gpu_block(BUS_B, (4300, 'C', 'job', '50 MiB')))
        per = nvidia_smi.attribute_pids([KEY_A, KEY_B], frozenset({4100, 4300}), run=smi_run(out))
        assert per == {KEY_B: {4300: 50}}
        assert nvidia_smi.sums([KEY_A, KEY_B], per) == {KEY_A: None, KEY_B: 50}

    def test_a_distrusted_listing_attributes_no_card(self, linux):
        out = listing(gpu_block('00000000:09:00.0', (4100, 'G', 'render-app', '600 MiB')))
        assert nvidia_smi.attribute_pids([KEY_A], frozenset({4100}), run=smi_run(out)) == {}

    def test_a_failed_call_gives_none(self, linux):
        assert nvidia_smi.attribute_pids([KEY_A], frozenset({4100}), run=smi_run(None)) is None
        assert nvidia_smi.attribute([KEY_A], frozenset({4100}), run=smi_run(None)) == {KEY_A: None}

    def test_windows_gives_none_never_zero(self, monkeypatch):
        monkeypatch.setattr(nvidia_smi, '_is_windows', lambda: True)
        calls = []
        assert nvidia_smi.attribute_pids([KEY_A], frozenset({4100}), run=calls.append) is None
        assert calls == []

    def test_the_backend_fills_per_pid_with_one_pids_call(self, linux):
        calls = []
        cards = f"0, {BUS_A}, 24564, 2000, Test Card A\n1, {BUS_B}, 24576, 100, Test Card B\n"

        def run(args):
            calls.append(args[1])
            return cards if args[1] == nvidia_smi.QUERY_GPU[1] else BUSY

        reading = nvidia_smi.NvidiaSmiBackend(run=run, clock=lambda: 1.0).sample(frozenset({4100, 4200}))
        assert reading.per_pid == {KEY_A: {4100: 600, 4200: 300}, KEY_B: {}}
        assert [gpu.sethlans_used_mb for gpu in reading.gpus] == [900, 0]
        assert calls.count(nvidia_smi.QUERY_PIDS[1]) == 1

    def test_the_backend_on_windows_has_no_map(self, monkeypatch):
        monkeypatch.setattr(nvidia_smi, '_is_windows', lambda: True)
        cards = f"0, {BUS_A}, 24564, 2000, Test Card A\n"
        reading = nvidia_smi.NvidiaSmiBackend(run=lambda args: cards, clock=lambda: 1.0).sample(frozenset({1}))
        assert reading.per_pid is None and reading.gpus[0].sethlans_used_mb is None


def test_per_pid_defaults_to_none_and_stays_out_of_the_hash():
    plain = GpuMemoryReading(1.0, 'fake-a', ())
    mapped = GpuMemoryReading(1.0, 'fake-a', (), per_pid={'pci:0000:01:00.0': {1: 2}})
    assert plain.per_pid is None
    assert hash(mapped) == hash(plain) and mapped != plain
