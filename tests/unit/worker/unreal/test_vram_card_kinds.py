# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Each card of the Unreal reading carries its kind (Unreal per-machine memory test D1, D7): ``vram`` keeps the
inventory card's ``vvvv:dddd:rrrr`` per PCI address with the decided set, and every kept card of a reading
gets it in ``GpuMem.card_kind`` (``None`` for a card without one), so the gate matches a card to this machine's
result by the card's own kind.
"""
from types import SimpleNamespace

import pytest

from sethlans_worker_agent.unreal_executor import vram

KEY_A, KEY_B, KEY_C = 'pci:0000:0a:00.0', 'pci:0000:41:00.0', 'pci:0000:62:00.0'
ADDR_A, ADDR_B, ADDR_C = (0, 0x0a, 0, 0), (0, 0x41, 0, 0), (0, 0x62, 0, 0)
A = vram.GpuMem(1, 'Radeon Test 20G', 20464, 300, None, None, KEY_A)
B = vram.GpuMem(2, 'GeForce Test 32G', 32607, 900, None, None, KEY_B)
C = vram.GpuMem(3, 'Arc Test 16G', 16384, 100, None, None, KEY_C)
LIVE = vram.GpuMemoryReading(7.0, 'test-backend', (A, B, C), measures_card=True)


@pytest.fixture(autouse=True)
def _fresh_set():
    vram.reset_for_tests()
    yield
    vram.reset_for_tests()


def _inventory():
    return (SimpleNamespace(key=KEY_A, vendor_id=0x1002, device_id=0x744c, revision_id=0xc8),
            SimpleNamespace(key='pci:00000000:41:00.0', vendor_id=0x10de, device_id=0x2b85, revision_id=None),
            SimpleNamespace(key=KEY_C, vendor_id=None, device_id=0x56a0, revision_id=8)), ''


def _decide():
    return vram.unreal_card_set(clock=lambda: 100.0, current=lambda: _inventory(), generation=lambda: 1)


class TestDecidedKinds:

    def test_each_inventory_card_has_its_kind_by_address(self):
        assert _decide() == frozenset({ADDR_A, ADDR_B, ADDR_C})
        assert vram.card_kinds() == {ADDR_A: '1002:744c:00c8', ADDR_B: '10de:2b85:none'}  # no vendor id: no kind

    def test_no_kinds_before_a_decision(self):
        assert vram.card_kinds() == {}

    def test_the_returned_map_is_a_copy(self):
        _decide()
        vram.card_kinds().clear()
        assert len(vram.card_kinds()) == 2


class TestReadingKinds:

    def test_with_screens_sets_each_cards_kind(self):
        reading = vram.with_screens(LIVE, {}, {ADDR_A: '1002:744c:00c8', ADDR_B: '10de:2b85:none'})
        assert [gpu.card_kind for gpu in reading.gpus] == ['1002:744c:00c8', '10de:2b85:none', None]
        assert [gpu.screen for gpu in reading.gpus] == [None, None, None]

    def test_without_kinds_every_card_has_none(self):
        assert all(gpu.card_kind is None for gpu in vram.with_screens(LIVE, {}).gpus)
        assert vram.with_screens(None, {}, {ADDR_A: '1002:744c:00c8'}) is None

    def test_take_reading_carries_the_decided_kinds(self):
        addresses = _decide()
        reading = vram.take_reading(0, clock=lambda: 50.0, read=lambda pids: LIVE, pids=frozenset,
                                    cards=lambda: addresses)
        kinds = {gpu.card_key: gpu.card_kind for gpu in reading.gpus}
        assert kinds == {KEY_A: '1002:744c:00c8', KEY_B: '10de:2b85:none', KEY_C: None}

    def test_the_override_reading_has_no_kind(self):
        _decide()
        reading = vram.take_reading(20480, clock=lambda: 1.0)
        assert [gpu.card_kind for gpu in reading.gpus] == [None]
