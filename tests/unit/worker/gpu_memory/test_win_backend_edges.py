# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
More of the Windows ``pdh`` backend with fakes (GPU memory reader FR-GW-4, FR-GW-7, FR-GW-8, FR-GI-2, AC-13,
AC-14): mixed brands with the built-in GPU, attribution over the worker and its descendants, the 60 s re-listing
rule, the LUID kept out of every log line, and ctypes failures that end as "no reading" through the reader.
Every key, LUID and pid is made up; runs on every OS.
"""
import ctypes
import dataclasses
import logging

import pytest

from sethlans_worker_agent.cards.os_probe import OsCard
from sethlans_worker_agent.gpu_memory import win_backend, win_pdh
from sethlans_worker_agent.gpu_memory.types import GpuMem
from tests.unit.worker.gpu_memory._reader_fakes import FakeBackend, make_reader, only
from tests.unit.worker.gpu_memory._win_fakes import (
    LUID_A, LUID_B, OTHER_PID, Clock, FakeApi, FakeCards, FakeQuery, adapter_item, collect, make_backend,
    process_item, two_cards)
from tests.unit.worker.gpu_memory._win_fakes import os_card as fake_card

LUID_C = (0x00000001, 0x0000BEEF)   # a non-zero high part
WORKER, CHILD, GRANDCHILD = 5100, 5101, 5102
FAMILY = frozenset({WORKER, CHILD, GRANDCHILD})


def _brand(index, key, name, vendor, mib):
    return OsCard(key=key, name=name, vendor_id=vendor, device_id=0x0001, dxgi_index=index, dedicated_mib=mib)


# Real PCI vendor ids (NVIDIA, AMD, Intel); names, keys and LUIDs made up.
MIXED = ((_brand(0, 'pci:0000:0c:00.0', 'Test NVIDIA-like Card', 0x10DE, 32607), LUID_A),
         (_brand(1, 'pci:0000:0d:00.0', 'Test AMD-like Card', 0x1002, 24560), LUID_B),
         (_brand(2, 'pci:0000:00:02.0', 'Test Intel-like Built-in GPU', 0x8086, 128), LUID_C))
MIXED_ADAPTERS = [adapter_item(LUID_A, 2000), adapter_item(LUID_B, 1500, upper=False), adapter_item(LUID_C, 64)]
MIXED_PROCESSES = [process_item(WORKER, LUID_A, 300), process_item(CHILD, LUID_B, 400),
                   process_item(CHILD, LUID_B, 100, phys=1), process_item(GRANDCHILD, LUID_C, 32),
                   process_item(OTHER_PID, LUID_B, 999), process_item(OTHER_PID, LUID_C, 16)]


class TestMixedBrands:

    def test_every_listed_card_with_an_instance_is_read_whatever_its_brand(self):
        backend = make_backend(FakeQuery([collect(MIXED_ADAPTERS, MIXED_PROCESSES)]), cards=FakeCards(MIXED))
        assert backend.open() is True
        reading = backend.sample(FAMILY)
        assert reading.source == 'pdh' and reading.measures_card is True
        assert [(gpu.index, gpu.name, gpu.total_mb, gpu.used_mb, gpu.card_key) for gpu in reading.gpus] == [
            (0, 'Test NVIDIA-like Card', 32607, 2000, 'pci:0000:0c:00.0'),
            (1, 'Test AMD-like Card', 24560, 1500, 'pci:0000:0d:00.0'),
            (2, 'Test Intel-like Built-in GPU', 128, 64, 'pci:0000:00:02.0')]

    def test_process_attribution_works_on_every_brand(self):
        """#226: per-process memory on AMD and Intel cards too, not only NVIDIA."""
        backend = make_backend(FakeQuery([collect(MIXED_ADAPTERS, MIXED_PROCESSES)]), cards=FakeCards(MIXED))
        assert backend.open() is True
        reading = backend.sample(FAMILY)
        assert reading.measures_process is True
        attribution = [(gpu.sethlans_used_mb, gpu.other_used_mb) for gpu in reading.gpus]
        assert attribution == [(300, 1700), (500, 1000), (32, 32)]

    def test_the_built_in_gpu_without_an_instance_is_dropped_and_the_others_kept(self):
        items = [adapter_item(LUID_A, 2000), adapter_item(LUID_B, 1500)]
        backend = make_backend(FakeQuery([collect(items, [])]), cards=FakeCards(MIXED))
        assert backend.open() is True
        assert [gpu.index for gpu in backend.sample(FAMILY).gpus] == [0, 1]


class TestAttributionOverTheProcessTree:

    def test_the_worker_and_every_descendant_are_summed_per_card(self):
        processes = [process_item(WORKER, LUID_A, 100), process_item(CHILD, LUID_A, 700),
                     process_item(CHILD, LUID_A, 50, phys=1), process_item(GRANDCHILD, LUID_B, 30),
                     process_item(OTHER_PID, LUID_A, 999), process_item(OTHER_PID, LUID_B, 999)]
        items = [adapter_item(LUID_A, 4000), adapter_item(LUID_B, 2000)]
        backend = make_backend(FakeQuery([collect(items, processes)]))
        backend.open()
        first, second = backend.sample(FAMILY).gpus
        assert (first.sethlans_used_mb, first.other_used_mb) == (850, 3150)
        assert (second.sethlans_used_mb, second.other_used_mb) == (30, 1970)

    def test_bad_process_items_are_skipped(self):
        processes = [process_item(WORKER, LUID_A, 100),
                     process_item(CHILD, LUID_A, 900, status=win_pdh.PDH_CSTATUS_NO_INSTANCE),
                     (f"pid_{CHILD}_luid_garbage_phys_0", 900 * 1024 * 1024, 0),
                     process_item(GRANDCHILD, (0, 0x0000DEAD), 900)]   # a LUID of no kept card
        backend = make_backend(FakeQuery([collect([adapter_item(LUID_A, 500), adapter_item(LUID_B, 100)],
                                                  processes)]))
        backend.open()
        assert [gpu.sethlans_used_mb for gpu in backend.sample(FAMILY).gpus] == [100, 0]

    def test_no_process_instance_is_zero_attribution_still_measured(self):
        backend = make_backend(FakeQuery([collect([adapter_item(LUID_A, 500), adapter_item(LUID_B, 100)], [])]))
        backend.open()
        reading = backend.sample(FAMILY)
        assert reading.measures_process is True and [gpu.sethlans_used_mb for gpu in reading.gpus] == [0, 0]


IDLE = collect([adapter_item(LUID_A, 500), adapter_item(LUID_B, 100)], [])
EXTRA = collect([adapter_item(LUID_A, 500), adapter_item(LUID_B, 100), adapter_item((0, 0x0000F00D), 1)], [])
B_GONE = collect([adapter_item(LUID_A, 500)], [])


class TestRelisting:

    def test_an_unknown_luid_within_60_s_keeps_reading_without_a_new_listing(self):
        cards, clock = FakeCards(), Clock()
        backend = make_backend(FakeQuery([IDLE, EXTRA]), cards=cards, clock=clock)
        backend.open()
        clock.now += 59
        assert [gpu.used_mb for gpu in backend.sample(FAMILY).gpus] == [500, 100]
        assert cards.calls == 1
        clock.now += 1
        assert [gpu.used_mb for gpu in backend.sample(FAMILY).gpus] == [500, 100]
        assert cards.calls == 2

    def test_an_unknown_luid_after_a_listing_waits_another_60_s(self):
        cards, clock = FakeCards(two_cards(), (), two_cards()), Clock()   # the second listing is empty
        backend = make_backend(FakeQuery([IDLE, EXTRA]), cards=cards, clock=clock)
        backend.open()
        clock.now += 60
        assert backend.sample(FAMILY) is not None and cards.calls == 2
        clock.now += 59
        assert backend.sample(FAMILY) is not None and cards.calls == 2
        clock.now += 1
        assert backend.sample(FAMILY) is not None and cards.calls == 3

    def test_a_card_gone_from_the_new_listing_restarts_then_reads_the_rest(self, caplog):
        caplog.set_level(logging.WARNING, logger=win_backend.__name__)
        cards, clock = FakeCards(two_cards(), two_cards()[:1]), Clock()
        backend = make_backend(FakeQuery([IDLE, B_GONE]), cards=cards, clock=clock)
        backend.open()
        clock.now += 60
        assert backend.sample(FAMILY) is None
        assert caplog.messages == ["GPU adapters changed; the reading restarts."]
        assert [gpu.card_key for gpu in backend.sample(FAMILY).gpus] == [two_cards()[0][0].key]


class TestTheLuidStaysInMemory:
    """FR-GI-2: the LUID is in no log line and no reading field."""

    def test_no_log_line_names_a_luid(self, caplog):
        caplog.set_level(logging.DEBUG)
        moved = (0, 0x0000F00D)
        new = (two_cards()[0], (fake_card(2, two_cards()[1][0].key, 'Test Card B', 4096), moved))
        lone = collect([adapter_item(LUID_A, 500), adapter_item(moved, 100)], [])
        cards, clock = FakeCards(two_cards()[:1] + ((fake_card(5, 'pci:0000:0e:00.0'), LUID_C),), new), Clock()
        backend = make_backend(FakeQuery([collect([adapter_item(LUID_A, 500)], []), lone]), cards=cards, clock=clock)
        backend.open()
        clock.now += 60
        backend.sample(FAMILY)
        backend.sample(FAMILY)
        assert caplog.messages, 'the scenario should log the dropped card and the change'
        text = ' '.join(caplog.messages).lower()
        for luid in (LUID_A, LUID_B, LUID_C, moved):
            value = win_backend.luid_value(luid)
            assert f'{luid[1]:08x}' not in text and str(value) not in text and f'{value:x}' not in text

    def test_no_reading_field_holds_a_luid(self):
        assert 'luid' not in {field.name for field in dataclasses.fields(GpuMem)}


class _RaisingFactory:
    def __init__(self, error):
        self.error = error

    def __call__(self, paths):
        raise self.error


class TestCtypesFailuresThroughTheReader:
    """A ctypes failure anywhere in the ``pdh`` backend gives the next source or no reading, never an exception."""

    @pytest.mark.parametrize('error', [ctypes.ArgumentError('bad argument'), OSError('access violation'),
                                       win_pdh.PdhError('no counters'), AttributeError('no export'),
                                       TypeError('bad type')])
    def test_a_query_that_fails_to_open_falls_back_to_nvidia_smi(self, monkeypatch, error):
        only(monkeypatch, 'pdh', 'nvidia-smi')
        log = []
        pdh = win_backend.PdhBackend(cards=FakeCards(), query_factory=_RaisingFactory(error), clock=Clock(),
                                     is_64bit=True, cross_check=lambda reading: None)
        reader, created = make_reader(pdh, FakeBackend('nvidia-smi', log), platform='win32')
        reading = reader.read(lambda: FAMILY)
        assert reading is not None and reading.source == 'nvidia-smi' and created == ['pdh', 'nvidia-smi']

    def test_a_real_query_whose_open_call_raises_falls_back(self, monkeypatch):
        class Api(FakeApi):
            def open_query(self, source, user, handle):
                raise ctypes.ArgumentError('argument 3: wrong type')

        only(monkeypatch, 'pdh', 'nvidia-smi')
        pdh = win_backend.PdhBackend(cards=FakeCards(), query_factory=lambda paths: win_pdh.PdhQuery(paths, api=Api()),
                                     clock=Clock(), is_64bit=True, cross_check=lambda reading: None)
        reader, _created = make_reader(pdh, FakeBackend('nvidia-smi', []), platform='win32')
        assert reader.read(lambda: FAMILY).source == 'nvidia-smi'

    def test_a_chosen_backend_whose_collect_raises_gives_none(self, monkeypatch):
        class Breaking(FakeQuery):
            def collect(self):
                if self.calls >= 2:
                    raise ctypes.ArgumentError('argument 1: wrong type')
                return super().collect()

        only(monkeypatch, 'pdh')
        pdh = make_backend(Breaking([IDLE]))
        reader, _created = make_reader(pdh, platform='win32')
        assert reader.read(lambda: FAMILY).source == 'pdh'
        assert reader.read(lambda: FAMILY) is None and reader.source == 'pdh'
