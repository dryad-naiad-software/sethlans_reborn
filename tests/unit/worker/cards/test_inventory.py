# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``cards.inventory.build`` joining an OS card list with Blender's
(FR-CI-6 rows for Windows and Linux, D2, the "Matching across lists" table).
The failed-probe fallback and macOS are in ``test_inventory_fallback_macos.py``.
"""
import logging

import pytest

from sethlans_worker_agent.cards import inventory
from tests.unit.worker.cards._card_ids import (
    KEY_5090, KEY_IGPU, KEY_XT_03, KEY_XT_23, RADEON_IGPU, RTX_5090_CUDA, RTX_5090_OPTIX, RX_7900_XT_03,
    RX_7900_XT_23, blender, os_card, records, two_xt_blender_cards, two_xt_os_cards,
)

LOGGER = 'sethlans_worker_agent.cards.inventory'


@pytest.fixture(params=['win32', 'linux'])
def platform_name(request):
    return request.param


def _by_key(inv):
    return {card.key: card for card in inv.cards}


class TestMatchedCards:

    def test_two_identical_cards_pair_by_address_not_by_order(self, platform_name):
        inv = inventory.build(two_xt_os_cards(), two_xt_blender_cards(), '4.5.3', platform_name=platform_name)

        assert [card.key for card in inv.cards] == [KEY_XT_03, KEY_XT_23]
        first, second = inv.cards
        assert first.blender == (RX_7900_XT_03, 'HIP')
        assert second.blender == (RX_7900_XT_23, 'HIP')
        assert (first.dxgi_index, second.dxgi_index) == (0, 1)
        assert first.note == second.note == ''
        assert (first.vendor_id, first.device_id, first.dedicated_mib) == (0x1002, 0x744C, 20464)
        assert inv.os_probe_failed is False
        assert inv.blender_version == '4.5.3'

    def test_optix_entry_matches_after_dropping_the_suffix(self, platform_name):
        os_cards = (os_card(KEY_5090, 'NVIDIA GeForce RTX 5090', 0x10DE, 0x2B85, dxgi_index=0),)

        inv = inventory.build(os_cards, [blender(RTX_5090_OPTIX, 'OPTIX', 'NVIDIA GeForce RTX 5090')], '4.5.3',
                              platform_name=platform_name)

        assert inv.cards[0].blender == (RTX_5090_OPTIX, 'OPTIX')
        assert inv.cards[0].note == ''

    def test_cuda_form_matches_too(self, platform_name):
        os_cards = (os_card(KEY_5090, 'NVIDIA GeForce RTX 5090'),)

        inv = inventory.build(os_cards, [blender(RTX_5090_CUDA, 'CUDA', 'x')], '4.5.3', platform_name=platform_name)

        assert inv.cards[0].blender == (RTX_5090_CUDA, 'CUDA')

    def test_built_in_gpu_is_a_card(self, platform_name):
        os_cards = (os_card(KEY_5090, 'NVIDIA GeForce RTX 5090'), os_card(KEY_IGPU, 'AMD Radeon(TM) Graphics'))
        blender_cards = [blender(RTX_5090_OPTIX, 'OPTIX', 'a'), blender(RADEON_IGPU, 'HIP', 'b')]

        cards = _by_key(inventory.build(os_cards, blender_cards, '4.5.3', platform_name=platform_name))

        assert cards[KEY_IGPU].blender == (RADEON_IGPU, 'HIP')
        assert cards[KEY_5090].blender == (RTX_5090_OPTIX, 'OPTIX')

    def test_function_digit_is_ignored(self, platform_name):
        os_cards = (os_card('pci:0000:01:00.1', 'odd function'),)

        inv = inventory.build(os_cards, [blender(RTX_5090_CUDA, 'CUDA', 'x')], '4.5.3', platform_name=platform_name)

        assert inv.cards[0].blender == (RTX_5090_CUDA, 'CUDA')

    @pytest.mark.parametrize('key, device_id', [
        ('pci:10000:01:00.0', 'ONEAPI_Intel Arc A770_10000:01:00'),         # five-digit domain
        ('pci:0000:01:00.0', 'CUDA_NVIDIA GeForce RTX 5090_00000000:01:00'),  # eight-digit domain, same value
        ('pci:0000:0a:00.0', 'HIP_AMD Radeon_0000:0A:00'),                   # upper case
        ('pci:0000:0a:00.0', 'HIP_AMD Radeon_0000:0a:00.0'),                 # trailing function
    ])
    def test_addresses_compare_as_integers(self, platform_name, key, device_id):
        inv = inventory.build((os_card(key, 'card'),), [blender(device_id, 'HIP', 'x')], '4.5.3',
                              platform_name=platform_name)

        assert inv.cards[0].blender == (device_id, 'HIP')

    def test_name_is_the_os_name_else_blender_else_the_key(self, platform_name):
        os_cards = (os_card(KEY_XT_03, 'OS name'), os_card(KEY_XT_23, ''), os_card(KEY_IGPU, ''))
        blender_cards = [blender(RX_7900_XT_03, 'HIP', 'Blender 03'), blender(RX_7900_XT_23, 'HIP', 'Blender 23')]

        cards = _by_key(inventory.build(os_cards, blender_cards, '4.5.3', platform_name=platform_name))

        assert cards[KEY_XT_03].name == 'OS name'
        assert cards[KEY_XT_23].name == 'Blender 23'
        assert cards[KEY_IGPU].name == KEY_IGPU


class TestUnmatched:

    def test_os_card_without_blender_match_is_not_offered(self, platform_name):
        os_cards = (os_card(KEY_5090, 'NVIDIA GeForce RTX 5090'), os_card(KEY_IGPU, 'AMD Radeon(TM) Graphics'))

        cards = _by_key(inventory.build(os_cards, [blender(RTX_5090_OPTIX, 'OPTIX', 'a')], '4.5.3',
                                        platform_name=platform_name))

        assert cards[KEY_IGPU].blender is None
        assert cards[KEY_IGPU].note == 'not offered by Blender 4.5.3'
        assert cards[KEY_5090].note == ''

    def test_not_offered_without_a_known_version(self, platform_name):
        inv = inventory.build((os_card(KEY_IGPU, 'iGPU'),), [], None, platform_name=platform_name)

        assert inv.cards[0].note == 'not offered by Blender'
        assert inventory.not_offered_note('5.0.1') == 'not offered by Blender 5.0.1'

    def test_blender_not_installed_marks_every_card_and_logs_nothing(self, platform_name, caplog):
        caplog.set_level(logging.DEBUG)

        inv = inventory.build(two_xt_os_cards(), None, None, platform_name=platform_name)

        assert [card.note for card in inv.cards] == ['Blender not installed yet'] * 2
        assert all(card.blender is None for card in inv.cards)
        assert inv.os_probe_failed is False
        assert records(caplog, LOGGER) == []

    def test_empty_os_list_gives_no_cards_and_untied_blender_devices(self, platform_name, caplog):
        caplog.set_level(logging.DEBUG)

        inv = inventory.build((), two_xt_blender_cards(), '4.5.3', platform_name=platform_name)

        assert inv.cards == ()
        assert inv.os_probe_failed is False
        assert len(records(caplog, LOGGER, 'WARNING')) == 2


class TestUntiedBlenderDevices:

    @pytest.mark.parametrize('device_id', [
        'CUDA_NVIDIA GeForce RTX 5090',        # no address
        'NVIDIA GeForce RTX 5090',             # bare name
        'HIP_AMD Radeon_0000:99:00',           # an address no OS card has
        'garbage',
    ])
    def test_device_that_cannot_be_tied_is_not_a_card_and_warns_once(self, platform_name, caplog, device_id):
        caplog.set_level(logging.DEBUG)

        for _ in range(2):
            inv = inventory.build(two_xt_os_cards(), [blender(device_id, 'HIP', 'x')], '4.5.3',
                                  platform_name=platform_name)

        assert [card.key for card in inv.cards] == [KEY_XT_03, KEY_XT_23]
        assert all(card.blender is None for card in inv.cards)
        assert [record.getMessage() for record in records(caplog, LOGGER)] == [
            f"Blender device '{device_id}' cannot be tied to a graphics card; it is not used."]

    def test_id_is_ascii_escaped_in_the_log(self, platform_name, caplog):
        caplog.set_level(logging.DEBUG)

        inventory.build((), [blender('HIP_Radeon™\n_0000:99:00', 'HIP', 'x')], '4.5.3',
                        platform_name=platform_name)

        message = records(caplog, LOGGER, 'WARNING')[0].getMessage()
        assert "'HIP_Radeon\\u2122\\n_0000:99:00'" in message
        assert message.isascii()

    @pytest.mark.parametrize('record', [
        'not a dict', {'id': RX_7900_XT_03}, {'type': 'HIP'}, {'id': 5, 'type': 'HIP'}, {'id': RX_7900_XT_03, 'type': None},
    ])
    def test_malformed_blender_records_are_ignored(self, platform_name, record):
        inv = inventory.build(two_xt_os_cards(), [record], '4.5.3', platform_name=platform_name)

        assert all(card.blender is None for card in inv.cards)

    def test_os_card_without_a_key_is_dropped_off_macos(self, platform_name):
        os_cards = (os_card(None, 'keyless'), os_card(KEY_XT_03, 'card'))

        assert [card.key for card in inventory.build(os_cards, [], '4.5.3', platform_name=platform_name).cards] == [
            KEY_XT_03]


class TestAmbiguous:

    def test_two_os_cards_at_one_pci_device_get_no_match_and_one_error(self, platform_name, caplog):
        os_cards = (os_card('pci:0000:01:00.0', 'function 0'), os_card('pci:0000:01:00.1', 'function 1'),
                    os_card(KEY_XT_23, 'other card'))
        blender_cards = [blender(RTX_5090_CUDA, 'CUDA', 'a'), blender(RX_7900_XT_23, 'HIP', 'b')]
        caplog.set_level(logging.DEBUG)

        for _ in range(2):
            cards = _by_key(inventory.build(os_cards, blender_cards, '4.5.3', platform_name=platform_name))

        assert cards['pci:0000:01:00.0'].blender is None
        assert cards['pci:0000:01:00.1'].blender is None
        assert cards['pci:0000:01:00.0'].note == inventory.NOTE_SHARED_DEVICE
        assert cards['pci:0000:01:00.1'].note == inventory.NOTE_SHARED_DEVICE
        assert cards[KEY_XT_23].blender == (RX_7900_XT_23, 'HIP')
        errors = records(caplog, LOGGER, 'ERROR')
        assert [record.getMessage() for record in errors] == [
            'Graphics cards pci:0000:01:00.0, pci:0000:01:00.1 share one PCI device; '
            'Blender cannot be tied to either of them.']
        assert records(caplog, LOGGER, 'WARNING') == []   # the device is ambiguous, not untied

    def test_shared_device_note_also_without_a_blender_device(self, platform_name):
        os_cards = (os_card('pci:0000:01:00.0', 'a'), os_card('pci:0000:01:00.1', 'b'))

        inv = inventory.build(os_cards, [], '4.5.3', platform_name=platform_name)

        assert [card.note for card in inv.cards] == [inventory.NOTE_SHARED_DEVICE] * 2

    def test_two_blender_devices_for_one_card_use_neither_and_log_an_error(self, platform_name, caplog):
        blender_cards = [blender(RTX_5090_OPTIX, 'OPTIX', 'a'), blender(RTX_5090_CUDA, 'CUDA', 'b')]
        caplog.set_level(logging.DEBUG)

        for _ in range(2):
            inv = inventory.build((os_card(KEY_5090, 'NVIDIA GeForce RTX 5090'),), blender_cards, '4.5.3',
                                  platform_name=platform_name)

        assert inv.cards[0].blender is None
        assert inv.cards[0].note == inventory.NOTE_LISTED_TWICE
        assert inv.cards[0].note == 'Blender lists this card twice; it cannot tell which entry is which'
        errors = records(caplog, LOGGER, 'ERROR')
        assert [record.getMessage() for record in errors] == [
            'Blender lists 2 devices for the graphics card pci:0000:01:00.0; none of them is used.']

    def test_build_holds_no_state_between_calls(self, platform_name):
        first = inventory.build(two_xt_os_cards(), two_xt_blender_cards(), '4.5.3', platform_name=platform_name)
        second = inventory.build(two_xt_os_cards(), two_xt_blender_cards(), '4.5.3', platform_name=platform_name)

        assert first == second
