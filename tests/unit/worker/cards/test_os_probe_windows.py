# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``cards.os_probe_windows`` (FR-CI-2, AC-1) with fake bindings.

``win_dxgi.enumerate_adapters``, ``win_adapter_info.available`` and
``win_adapter_info.pci_address`` are replaced, so no DLL is loaded and the
tests run on every OS.
"""
import logging

import pytest

from sethlans_worker_agent.cards import os_probe, os_probe_windows
from sethlans_worker_agent.gpu_memory import win_adapter_info, win_dxgi
from sethlans_worker_agent.gpu_memory.win_dxgi import AdapterDesc
from tests.unit.worker.cards._card_ids import KEY_XT_03, KEY_XT_23, records

LOGGER = 'sethlans_worker_agent.cards.os_probe_windows'
GIB = 1024 ** 3
SOFTWARE = win_dxgi.DXGI_ADAPTER_FLAG_SOFTWARE


def _adapter(index, name, address, dedicated=20 * GIB, flags=0, vendor=0x1002, device=0x744C, revision=0xC8):
    """An adapter; ``address`` is what the fake D3DKMT reports for its LUID."""
    return AdapterDesc(index, name, vendor, device, dedicated, (0, 1000 + index), flags, revision), address


@pytest.fixture
def fake_bindings(mocker):
    """Install a fake adapter list: ``install([(AdapterDesc, address), ...], available=True)``."""
    state = {}

    def install(pairs, available=True):
        adapters = None if pairs is None else tuple(adapter for adapter, _address in pairs)
        by_luid = {adapter.luid: address for adapter, address in (pairs or ())}
        state['enumerate'] = mocker.patch.object(win_dxgi, 'enumerate_adapters', return_value=adapters)
        state['available'] = mocker.patch.object(win_adapter_info, 'available', return_value=available)
        state['pci_address'] = mocker.patch.object(win_adapter_info, 'pci_address',
                                                   side_effect=lambda luid: by_luid[luid])
        return state

    return install


def _two_xt_adapters():
    """Two identical cards with a virtual adapter first and the software adapter between them."""
    return [
        _adapter(0, 'AMD Radeon RX 7900 XT', None),                                  # virtual: no PCI address
        _adapter(1, 'AMD Radeon RX 7900 XT', '0000:03:00.0'),
        _adapter(2, 'Microsoft Basic Render Driver', None, dedicated=0, flags=SOFTWARE,
                 vendor=0x1414, device=0x8C),
        _adapter(3, 'AMD Radeon RX 7900 XT', '0000:23:00.0'),
    ]


class TestCards:

    def test_lists_cards_with_raw_dxgi_indexes(self, fake_bindings):
        fake_bindings(_two_xt_adapters())

        cards = os_probe_windows.probe()

        assert [card.key for card in cards] == [KEY_XT_03, KEY_XT_23]
        assert [card.dxgi_index for card in cards] == [1, 3]   # the virtual and software adapters count
        first = cards[0]
        assert first.name == 'AMD Radeon RX 7900 XT'
        assert (first.vendor_id, first.device_id) == (0x1002, 0x744C)
        assert first.dedicated_mib == 20 * 1024
        assert first.display is None

    def test_cards_stay_in_dxgi_order_until_os_probe_sorts_them(self, fake_bindings):
        fake_bindings([_adapter(0, 'B', '0000:23:00.0'), _adapter(1, 'A', '0000:03:00.0')])

        assert [card.key for card in os_probe_windows.probe()] == [KEY_XT_23, KEY_XT_03]
        sorted_cards = os_probe.probe(platform_name='win32')
        assert [(card.key, card.dxgi_index) for card in sorted_cards] == [(KEY_XT_03, 1), (KEY_XT_23, 0)]

    def test_software_flag_alone_excludes_an_adapter(self, fake_bindings):
        fake_bindings([_adapter(0, 'Software with memory', '0000:05:00.0', flags=SOFTWARE),
                       _adapter(1, 'Card', '0000:23:00.0', flags=0x1)])   # 0x1 = REMOTE, not software

        assert [card.key for card in os_probe_windows.probe()] == [KEY_XT_23]

    def test_zero_dedicated_memory_is_not_a_card(self, fake_bindings):
        fake_bindings([_adapter(0, 'No memory', '0000:03:00.0', dedicated=0),
                       _adapter(1, 'Card', '0000:23:00.0')])

        assert [card.key for card in os_probe_windows.probe()] == [KEY_XT_23]

    def test_less_than_one_mib_is_still_a_card(self, fake_bindings):
        fake_bindings([_adapter(0, 'Tiny', '0000:03:00.0', dedicated=1)])

        assert [card.dedicated_mib for card in os_probe_windows.probe()] == [0]

    def test_all_ones_address_is_not_a_card(self, fake_bindings):
        fake_bindings([_adapter(0, 'All ones', '0000:ffffffff:ffff.ffff'), _adapter(1, 'Card', '0000:03:00.0')])

        assert [card.key for card in os_probe_windows.probe()] == [KEY_XT_03]

    def test_virtual_adapter_without_address_is_not_a_card(self, fake_bindings):
        bindings = fake_bindings([_adapter(0, 'AMD Radeon RX 7900 XT', None)])

        assert os_probe_windows.probe() == ()
        bindings['pci_address'].assert_called_once_with((0, 1000))

    def test_software_adapter_is_not_asked_for_an_address(self, fake_bindings):
        bindings = fake_bindings([_adapter(0, 'Microsoft Basic Render Driver', None, dedicated=0, flags=SOFTWARE)])

        assert os_probe_windows.probe() == ()
        bindings['pci_address'].assert_not_called()

    def test_name_is_cleaned(self, fake_bindings):
        fake_bindings([_adapter(0, 'AMD\x00 Radeon\r\nRX', '0000:03:00.0')])

        assert os_probe_windows.probe()[0].name == 'AMD Radeon RX'

    @pytest.mark.parametrize('revision', [0xC8, 0, None])
    def test_revision_is_passed_through(self, fake_bindings, revision):
        fake_bindings([_adapter(0, 'Card', '0000:03:00.0', revision=revision)])

        assert os_probe_windows.probe()[0].revision_id == revision

    def test_adapter_desc_revision_defaults_to_none(self):
        assert AdapterDesc(0, 'x', 1, 2, 3, (0, 0), 0).revision is None

    def test_no_adapters_is_empty_not_none(self, fake_bindings):
        fake_bindings([])

        assert os_probe_windows.probe() == ()


class TestDuplicateKey:

    def test_both_adapters_with_one_key_are_dropped_with_one_error(self, fake_bindings, caplog):
        fake_bindings([_adapter(0, 'A', '0000:03:00.0'), _adapter(1, 'B', '0000:03:00.0'),
                       _adapter(2, 'C', '0000:23:00.0')])
        caplog.set_level(logging.DEBUG)

        first = os_probe_windows.probe()
        second = os_probe_windows.probe()

        assert [card.key for card in first] == [KEY_XT_23]
        assert first == second
        errors = records(caplog, LOGGER, 'ERROR')
        assert [record.getMessage() for record in errors] == [
            '2 graphics adapters report the PCI address pci:0000:03:00.0; none of them is used.']

    def test_duplicate_through_different_spellings_of_one_address(self, fake_bindings):
        fake_bindings([_adapter(0, 'A', '0000:0A:00.0'), _adapter(1, 'B', '0000:0a:00.0')])

        assert os_probe_windows.probe() == ()


class TestFailures:

    def test_no_dxgi_list_is_none(self, fake_bindings):
        bindings = fake_bindings(None)

        assert os_probe_windows.probe() is None
        bindings['pci_address'].assert_not_called()

    def test_d3dkmt_unavailable_is_none_not_empty(self, fake_bindings):
        bindings = fake_bindings(_two_xt_adapters(), available=False)

        result = os_probe_windows.probe()

        assert result is None
        bindings['pci_address'].assert_not_called()

    def test_d3dkmt_unavailable_through_os_probe_is_none(self, fake_bindings, caplog):
        fake_bindings(_two_xt_adapters(), available=False)
        caplog.set_level(logging.DEBUG)

        assert os_probe.probe(platform_name='win32') is None
        assert [record.getMessage() for record in records(caplog, 'sethlans_worker_agent.cards.os_probe')] == [
            'Could not list the graphics cards through the operating system.']

    @pytest.mark.parametrize('target', ['enumerate_adapters', 'available', 'pci_address'])
    def test_any_exception_is_none_and_one_log_line(self, mocker, fake_bindings, caplog, target):
        fake_bindings(_two_xt_adapters())
        module = win_dxgi if target == 'enumerate_adapters' else win_adapter_info
        mocker.patch.object(module, target, side_effect=RuntimeError('binding broke'))
        caplog.set_level(logging.DEBUG)

        assert os_probe.probe(platform_name='win32') is None
        assert os_probe.probe(platform_name='win32') is None

        logged = [record for record in caplog.records if record.name.startswith('sethlans_worker_agent.cards')]
        assert len(logged) == 1
        assert logged[0].levelname == 'WARNING'
        assert logged[0].exc_info[0] is RuntimeError

    def test_malformed_adapter_record_is_none(self, mocker, fake_bindings):
        fake_bindings([])
        mocker.patch.object(win_dxgi, 'enumerate_adapters', return_value=(object(),))

        assert os_probe.probe(platform_name='win32') is None
