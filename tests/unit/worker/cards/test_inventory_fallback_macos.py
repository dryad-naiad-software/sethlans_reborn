# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``cards.inventory.build`` when the OS probe failed on Windows
or Linux (the inventory comes from Blender devices with an address) and on
macOS (D9: ``apple-gpu`` only with exactly one GPU and one ``METAL`` device).
"""
import logging

import pytest

from sethlans_worker_agent.cards import inventory
from sethlans_worker_agent.cards.identity import APPLE_GPU_KEY
from sethlans_worker_agent.cards.os_probe import OsCard
from tests.unit.worker.cards._card_ids import (
    KEY_5090, KEY_IGPU, KEY_XT_03, KEY_XT_23, RADEON_IGPU, RTX_5090_OPTIX, RX_7900_XT_03, RX_7900_XT_23, blender,
    records, two_xt_blender_cards,
)

LOGGER = 'sethlans_worker_agent.cards.inventory'
FALLBACK_WARNING = ('Could not list the graphics cards through the operating system; using the cards Blender '
                    'reports at a PCI address.')
METAL_ID = 'METAL_Apple M2 Max'


@pytest.fixture(params=['win32', 'linux'])
def platform_name(request):
    return request.param


class TestProbeFailedFallback:

    def test_cards_come_from_blender_devices_with_an_address(self, platform_name):
        inv = inventory.build(None, two_xt_blender_cards(), '4.5.3', platform_name=platform_name)

        assert inv.os_probe_failed is True
        assert inv.blender_version == '4.5.3'
        assert [card.key for card in inv.cards] == [KEY_XT_03, KEY_XT_23]   # address order, not Blender order
        assert [card.blender for card in inv.cards] == [(RX_7900_XT_03, 'HIP'), (RX_7900_XT_23, 'HIP')]
        for card in inv.cards:
            assert card.vendor_id is None and card.device_id is None
            assert card.dxgi_index is None and card.dedicated_mib is None
            assert card.note == ''
            assert card.name == 'AMD Radeon RX 7900 XT'

    def test_key_uses_function_zero_and_keeps_the_optix_entry(self, platform_name):
        inv = inventory.build(None, [blender(RTX_5090_OPTIX, 'OPTIX', 'NVIDIA GeForce RTX 5090'),
                                     blender(RADEON_IGPU, 'HIP', '')], '4.5.3', platform_name=platform_name)

        assert [(card.key, card.blender, card.name) for card in inv.cards] == [
            (KEY_5090, (RTX_5090_OPTIX, 'OPTIX'), 'NVIDIA GeForce RTX 5090'),
            (KEY_IGPU, (RADEON_IGPU, 'HIP'), KEY_IGPU),   # no name: the key
        ]

    def test_five_digit_domain_sorts_after_four_digit_ones(self, platform_name):
        inv = inventory.build(None, [blender('ONEAPI_Arc_10000:01:00', 'ONEAPI', 'a'),
                                     blender(RX_7900_XT_23, 'HIP', 'b')], '4.5.3', platform_name=platform_name)

        assert [card.key for card in inv.cards] == [KEY_XT_23, 'pci:10000:01:00.0']

    def test_one_warning_per_process(self, platform_name, caplog):
        caplog.set_level(logging.DEBUG)

        for _ in range(3):
            inventory.build(None, two_xt_blender_cards(), '4.5.3', platform_name=platform_name)

        assert [record.getMessage() for record in records(caplog, LOGGER, 'WARNING')] == [FALLBACK_WARNING]

    def test_device_without_address_is_untied(self, platform_name, caplog):
        caplog.set_level(logging.DEBUG)

        inv = inventory.build(None, [blender('CUDA_NVIDIA GeForce RTX 5090', 'CUDA', 'x'),
                                     blender(RX_7900_XT_03, 'HIP', 'y')], '4.5.3', platform_name=platform_name)

        assert [card.key for card in inv.cards] == [KEY_XT_03]
        assert [record.getMessage() for record in records(caplog, LOGGER, 'WARNING')] == [
            FALLBACK_WARNING,
            "Blender device 'CUDA_NVIDIA GeForce RTX 5090' cannot be tied to a graphics card; it is not used."]

    def test_two_devices_at_one_address_are_both_dropped_with_an_error(self, platform_name, caplog):
        caplog.set_level(logging.DEBUG)

        inv = inventory.build(None, [blender('CUDA_NVIDIA GeForce RTX 5090_0000:01:00', 'CUDA', 'a'),
                                     blender(RTX_5090_OPTIX, 'OPTIX', 'b'),
                                     blender(RX_7900_XT_23, 'HIP', 'c')], '4.5.3', platform_name=platform_name)

        assert [card.key for card in inv.cards] == [KEY_XT_23]
        assert [record.getMessage() for record in records(caplog, LOGGER, 'ERROR')] == [
            'Blender lists 2 devices at pci:0000:01:00.0; none of them is used.']

    def test_no_blender_either_gives_an_empty_failed_inventory(self, platform_name):
        inv = inventory.build(None, None, None, platform_name=platform_name)

        assert inv.cards == ()
        assert inv.os_probe_failed is True

    def test_capped_at_sixteen_cards(self, platform_name):
        devices = [blender(f'HIP_Card_0000:{bus:02x}:00', 'HIP', 'x') for bus in range(20, 0, -1)]

        inv = inventory.build(None, devices, '4.5.3', platform_name=platform_name)

        assert [card.key for card in inv.cards] == [f'pci:0000:{bus:02x}:00.0' for bus in range(1, 17)]


def _gpu(name='Apple M2 Max', display=True):
    return OsCard(key=None, name=name, display=display)


class TestMacos:

    def test_one_gpu_and_one_metal_device_form_apple_gpu(self):
        inv = inventory.build((_gpu(),), [blender(METAL_ID, 'METAL', 'Apple M2 Max (GPU - 38 cores)')], '4.5.3',
                              platform_name='darwin')

        assert len(inv.cards) == 1
        card = inv.cards[0]
        assert card.key == APPLE_GPU_KEY
        assert card.name == 'Apple M2 Max'
        assert card.blender == (METAL_ID, 'METAL')
        assert card.note == ''
        assert card.dxgi_index is None
        assert inv.os_probe_failed is False

    def test_os_name_missing_takes_the_blender_name(self):
        inv = inventory.build((_gpu(name=''),), [blender(METAL_ID, 'METAL', 'Apple M2 Max')], '4.5.3',
                              platform_name='darwin')

        assert inv.cards[0].name == 'Apple M2 Max'

    def test_only_metal_devices_count(self):
        blender_cards = [blender(METAL_ID, 'METAL', 'a'), blender('ONEAPI_x_0000:01:00', 'ONEAPI', 'b')]

        inv = inventory.build((_gpu(),), blender_cards, '4.5.3', platform_name='darwin')

        assert inv.cards[0].blender == (METAL_ID, 'METAL')

    @pytest.mark.parametrize('metal_count', [0, 2, 3])
    def test_other_metal_counts_offer_no_card_and_log_why_once(self, caplog, metal_count):
        blender_cards = [blender(f'METAL_GPU {n}', 'METAL', 'x') for n in range(metal_count)]
        caplog.set_level(logging.DEBUG)

        for _ in range(2):
            inv = inventory.build((_gpu(),), blender_cards, '4.5.3', platform_name='darwin')

        assert inv.cards == ()
        assert inv.os_probe_failed is False
        assert [record.getMessage() for record in records(caplog, LOGGER, 'WARNING')] == [
            f'Blender lists {metal_count} Metal devices; a card is offered only when there is exactly one.']

    @pytest.mark.parametrize('gpus', [(), (_gpu('Intel UHD Graphics 630'), _gpu('AMD Radeon Pro 5500M'))])
    def test_zero_or_two_os_gpus_offer_no_card_and_log_why(self, caplog, gpus):
        caplog.set_level(logging.DEBUG)

        inv = inventory.build(gpus, [blender(METAL_ID, 'METAL', 'a')], '4.5.3', platform_name='darwin')

        assert inv.cards == ()
        assert [record.getMessage() for record in records(caplog, LOGGER, 'WARNING')] == [
            f'macOS lists {len(gpus)} GPUs; a card is offered only when there is exactly one.']

    def test_blender_not_installed_yet(self, caplog):
        caplog.set_level(logging.DEBUG)

        inv = inventory.build((_gpu(),), None, None, platform_name='darwin')

        assert [(card.key, card.name, card.blender, card.note) for card in inv.cards] == [
            (APPLE_GPU_KEY, 'Apple M2 Max', None, 'Blender not installed yet')]
        assert records(caplog, LOGGER) == []

    def test_failed_probe_offers_no_card(self, caplog):
        caplog.set_level(logging.DEBUG)

        inv = inventory.build(None, [blender(METAL_ID, 'METAL', 'a')], '4.5.3', platform_name='darwin')

        assert inv.cards == ()
        assert inv.os_probe_failed is True
        assert len(records(caplog, LOGGER, 'WARNING')) == 1

    def test_default_platform_is_sys_platform(self, mocker):
        mocker.patch.object(inventory, 'sys').platform = 'darwin'

        inv = inventory.build((_gpu(),), [blender(METAL_ID, 'METAL', 'a')], '4.5.3')

        assert [card.key for card in inv.cards] == [APPLE_GPU_KEY]
