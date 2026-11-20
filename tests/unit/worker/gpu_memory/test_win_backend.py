# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Windows ``pdh`` backend with a fake ``PdhQuery`` and a fake card list (GPU memory reader FR-GW-4..10, D4, D6,
D21, AC-12, AC-13, AC-14). No DLL is loaded, so these run on every OS.
"""
import ctypes
import logging

import pytest

from sethlans_worker_agent.gpu_memory import win_backend
from sethlans_worker_agent.gpu_memory.win_pdh import PDH_CSTATUS_NEW_DATA, PDH_CSTATUS_NO_INSTANCE, PdhError
from tests.unit.worker.gpu_memory._win_fakes import (
    KEY_A, KEY_B, LUID_A, LUID_B, OTHER_PID, OURS, FakeCards, FakeQuery, adapter_item, collect, make_backend,
    os_card, process_item)

LOG = 'sethlans_worker_agent.gpu_memory.win_backend'
IDLE = collect([adapter_item(LUID_A, 500), adapter_item(LUID_B, 100)], [])


class TestNames:

    @pytest.mark.parametrize('name', ['luid_0x00000000_0x0001A2B3_phys_0', 'luid_0x00000000_0x0001a2b3_phys_1',
                                      'LUID_0X00000000_0X0001A2B3_PHYS_0'])
    def test_adapter_luid_ignores_case(self, name):
        assert win_backend.adapter_luid(name) == 0x0001A2B3

    @pytest.mark.parametrize('name', ['', 'luid_0x0_0x1_phys_0', 'luid_0x00000000_0x0001A2B3_phys_',
                                      'pid_1_luid_0x00000000_0x0001A2B3_phys_0', ' luid_0x00000000_0x0001A2B3_phys_0',
                                      'luid_0x00000000_0x0001A2B3_phys_0_extra', None])
    def test_other_adapter_names_do_not_parse(self, name):
        assert win_backend.adapter_luid(name) is None

    def test_process_owner(self):
        assert win_backend.process_owner('pid_4242_luid_0x00000001_0x0001a2b3_phys_0') == (4242, (1 << 32) | 0x1A2B3)
        assert win_backend.process_owner('pid_x_luid_0x00000001_0x0001a2b3_phys_0') is None
        assert win_backend.process_owner('luid_0x00000001_0x0001a2b3_phys_0') is None

    def test_luid_value_takes_the_high_part_unsigned(self):
        assert win_backend.luid_value((-1, 5)) == (0xFFFFFFFF << 32) | 5
        assert win_backend.luid_value((0, 0x0001A2B3)) == win_backend.adapter_luid('luid_0x00000000_0x0001A2B3_phys_0')


class TestOpen:

    def test_opens_both_counters_and_takes_the_first_collect(self):
        query = FakeQuery([IDLE])
        backend = make_backend(query)
        assert backend.open() is True
        assert query.paths == (r'\GPU Adapter Memory(*)\Dedicated Usage', r'\GPU Process Memory(*)\Dedicated Usage')
        assert query.calls == 1

    def test_a_card_without_an_adapter_instance_is_left_out(self, caplog):
        caplog.set_level(logging.INFO, logger=LOG)
        backend = make_backend(FakeQuery([collect([adapter_item(LUID_A, 500)], [])]))
        assert backend.open() is True
        reading = backend.sample(OURS)
        assert [gpu.card_key for gpu in reading.gpus] == [KEY_A]
        assert ("Card pci:0000:0b:00.0 'Test Card B' has no GPU Adapter Memory counter; it is left out of the GPU "
                "memory reading.") in caplog.messages

    def test_no_card_with_an_instance_does_not_open(self):
        assert make_backend(FakeQuery([collect([], [])])).open() is False

    @pytest.mark.parametrize('cards', [FakeCards(None), FakeCards(()), FakeCards(RuntimeError('probe broke'))])
    def test_no_card_list_does_not_open_or_open_a_query(self, cards):
        query = FakeQuery([IDLE])
        assert make_backend(query, cards=cards).open() is False
        assert query.paths is None

    def test_32_bit_python_does_not_open(self):
        cards = FakeCards()
        assert make_backend(FakeQuery([IDLE]), cards=cards, is_64bit=False).open() is False
        assert cards.calls == 0

    def test_a_query_that_cannot_open_does_not_open(self):
        def refuse(paths):
            raise PdhError('no counters')

        assert make_backend(refuse).open() is False

    @pytest.mark.parametrize('first', [None, {win_backend.ADAPTER_PATH: None, win_backend.PROCESS_PATH: []}])
    def test_a_failed_first_collect_does_not_open(self, first):
        assert make_backend(FakeQuery([first])).open() is False

    def test_a_query_argument_error_does_not_open_and_is_logged(self, caplog):
        caplog.set_level(logging.INFO, logger=LOG)

        def refuse(paths):
            raise ctypes.ArgumentError('argument 2: wrong type')

        assert make_backend(refuse).open() is False
        assert "The Windows GPU memory counters cannot be opened: argument 2: wrong type" in caplog.messages

    def test_at_most_16_cards(self):
        pairs = tuple((os_card(i, f'pci:0000:{i + 1:02x}:00.0', f'Card {i}'), (0, 100 + i)) for i in range(18))
        items = [adapter_item((0, 100 + i), 10) for i in range(18)]
        backend = make_backend(FakeQuery([collect(items, [])]), cards=FakeCards(pairs))
        assert backend.open() is True
        assert len(backend.sample(OURS).gpus) == 16


class TestCardUsed:

    def test_reads_every_card_with_dxgi_facts(self):
        backend = make_backend(FakeQuery([IDLE]))
        backend.open()
        reading = backend.sample(OURS)
        assert reading.source == 'pdh' and reading.measures_card is True and reading.measures_process is True
        assert [(gpu.index, gpu.name, gpu.total_mb, gpu.used_mb, gpu.card_key) for gpu in reading.gpus] == [
            (0, 'Test Card A', 8192, 500, KEY_A), (2, 'Test Card B', 4096, 100, KEY_B)]

    def test_every_phys_instance_of_a_luid_is_summed_whatever_the_hex_case(self):
        items = [adapter_item(LUID_A, 300, phys=0), adapter_item(LUID_A, 200, phys=1, upper=False),
                 adapter_item(LUID_B, 100, status=PDH_CSTATUS_NEW_DATA)]
        backend = make_backend(FakeQuery([collect(items, [])]))
        backend.open()
        assert [gpu.used_mb for gpu in backend.sample(OURS).gpus] == [500, 100]

    def test_items_with_an_invalid_status_are_skipped(self):
        items = [adapter_item(LUID_A, 500), adapter_item(LUID_A, 9000, phys=1, status=PDH_CSTATUS_NO_INSTANCE),
                 adapter_item(LUID_B, 100), ('garbage', 5, 0)]
        backend = make_backend(FakeQuery([collect(items, [])]))
        backend.open()
        assert [gpu.used_mb for gpu in backend.sample(OURS).gpus] == [500, 100]

    def test_a_kept_card_without_a_valid_item_makes_the_sample_none(self):
        partial = collect([adapter_item(LUID_A, 500), adapter_item(LUID_B, 100, status=PDH_CSTATUS_NO_INSTANCE)], [])
        backend = make_backend(FakeQuery([IDLE, partial]))
        backend.open()
        assert backend.sample(OURS) is None

    def test_used_is_clamped_to_the_dxgi_total(self):
        backend = make_backend(FakeQuery([collect([adapter_item(LUID_A, 9000), adapter_item(LUID_B, 100)], [])]))
        backend.open()
        assert backend.sample(OURS).gpus[0].used_mb == 8192

    @pytest.mark.parametrize('result', [None, {win_backend.ADAPTER_PATH: None, win_backend.PROCESS_PATH: []}])
    def test_a_failed_collect_or_adapter_array_is_none(self, result):
        backend = make_backend(FakeQuery([IDLE, result]))
        backend.open()
        assert backend.sample(OURS) is None

    def test_sample_after_close_is_none_and_close_is_once(self):
        query = FakeQuery([IDLE])
        backend = make_backend(query)
        backend.open()
        backend.close()
        backend.close()
        assert backend.sample(OURS) is None and query.closed == 1


class TestAttribution:

    def test_only_our_pids_on_the_cards_own_luid(self):
        processes = [process_item(4242, LUID_A, 300), process_item(4343, LUID_A, 100, phys=1),
                     process_item(OTHER_PID, LUID_A, 50), process_item(OTHER_PID, LUID_B, 60)]
        backend = make_backend(FakeQuery([collect([adapter_item(LUID_A, 500), adapter_item(LUID_B, 100)], processes)]))
        backend.open()
        first, second = backend.sample(OURS).gpus
        assert (first.sethlans_used_mb, first.other_used_mb) == (400, 100)
        assert (second.sethlans_used_mb, second.other_used_mb) == (0, 100)

    def test_a_failed_process_array_gives_no_attribution(self):
        backend = make_backend(FakeQuery([collect([adapter_item(LUID_A, 500), adapter_item(LUID_B, 100)], None)]))
        backend.open()
        reading = backend.sample(OURS)
        assert reading.measures_process is False
        assert all(gpu.sethlans_used_mb is None and gpu.other_used_mb is None for gpu in reading.gpus)

    def test_attribution_is_clamped_to_the_total(self):
        items = [adapter_item(LUID_A, 500), adapter_item(LUID_B, 100)]
        backend = make_backend(FakeQuery([collect(items, [process_item(4242, LUID_B, 5000)])]))
        backend.open()
        assert backend.sample(OURS).gpus[1].sethlans_used_mb == 4096


class TestCrossCheck:
    """FR-GS-6: once, after the first good sample, on a fresh collect, never in ``open()`` or a sample."""

    def test_runs_once_in_before_sample_after_the_first_good_sample(self):
        checks = []
        later = collect([adapter_item(LUID_A, 640), adapter_item(LUID_B, 100)], [process_item(4242, LUID_A, 300)])
        query = FakeQuery([IDLE, IDLE, later])
        backend = make_backend(query, checks=checks)
        backend.open()
        backend.before_sample()
        assert checks == []                       # not before the first sample
        backend.sample(OURS)
        assert checks == []                       # not inside a sample
        backend.before_sample()
        backend.before_sample()
        assert len(checks) == 1 and query.calls == 3
        assert [gpu.used_mb for gpu in checks[0].gpus] == [640, 100]   # its own fresh collect
        assert checks[0].measures_process is False and checks[0].gpus[0].sethlans_used_mb is None

    def test_not_due_while_samples_fail(self):
        checks = []
        backend = make_backend(FakeQuery([IDLE, None]), checks=checks)
        backend.open()
        backend.sample(OURS)
        backend.before_sample()
        assert checks == []

    def test_a_failed_collect_uses_up_the_check_without_calling_it(self):
        checks = []
        query = FakeQuery([IDLE, IDLE, None])
        backend = make_backend(query, checks=checks)
        backend.open()
        backend.sample(OURS)
        backend.before_sample()
        backend.before_sample()
        assert checks == [] and query.calls == 3
