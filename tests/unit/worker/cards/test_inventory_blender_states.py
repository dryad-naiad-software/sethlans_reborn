# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the three states of Blender's device list in
``cards.inventory.build``: not installed yet (``None``), the check failed
(``BLENDER_CHECK_FAILED``) and a real list (possibly empty), on Windows, Linux
and macOS; and the PCI revision id carried from the OS card to the ``Card``.
"""
import logging

import pytest

from sethlans_worker_agent.cards import inventory
from sethlans_worker_agent.cards.identity import APPLE_GPU_KEY
from sethlans_worker_agent.cards.inventory import BLENDER_CHECK_FAILED
from sethlans_worker_agent.cards.os_probe import OsCard
from tests.unit.worker.cards._card_ids import (
    KEY_XT_03, KEY_XT_23, RX_7900_XT_03, records, two_xt_blender_cards, two_xt_os_cards,
)

LOGGER = 'sethlans_worker_agent.cards.inventory'
FAILED_NOTE = 'Blender could not list its devices; see the log'


@pytest.fixture(params=['win32', 'linux'])
def platform_name(request):
    return request.param


def _apple(name='Apple M2 Max'):
    return OsCard(key=None, name=name, display=True)


class TestPcBlenderStates:

    def test_failed_check_marks_every_card_and_ties_nothing(self, platform_name, caplog):
        caplog.set_level(logging.DEBUG)

        inv = inventory.build(two_xt_os_cards(), BLENDER_CHECK_FAILED, None, platform_name=platform_name)

        assert [card.key for card in inv.cards] == [KEY_XT_03, KEY_XT_23]
        assert [card.note for card in inv.cards] == [FAILED_NOTE, FAILED_NOTE]
        assert inventory.NOTE_BLENDER_FAILED == FAILED_NOTE
        assert all(card.blender is None for card in inv.cards)
        assert [card.dxgi_index for card in inv.cards] == [0, 1]
        assert inv.os_probe_failed is False
        assert records(caplog, LOGGER) == []   # no "cannot be tied" lines for a list that does not exist

    def test_failed_check_wins_over_a_shared_device_note(self, platform_name):
        os_cards = (OsCard(key='pci:0000:01:00.0', name='a'), OsCard(key='pci:0000:01:00.1', name='b'))

        inv = inventory.build(os_cards, BLENDER_CHECK_FAILED, None, platform_name=platform_name)

        assert [card.note for card in inv.cards] == [FAILED_NOTE, FAILED_NOTE]

    def test_both_probes_failed_gives_an_empty_failed_inventory(self, platform_name):
        inv = inventory.build(None, BLENDER_CHECK_FAILED, None, platform_name=platform_name)

        assert inv.cards == ()
        assert inv.os_probe_failed is True

    @pytest.mark.parametrize('blender_cards, expected_note', [
        (None, 'Blender not installed yet'),
        ([], 'not offered by Blender 4.5.3'),
        (BLENDER_CHECK_FAILED, FAILED_NOTE),
    ])
    def test_each_state_keeps_its_own_note(self, platform_name, blender_cards, expected_note):
        inv = inventory.build(two_xt_os_cards(), blender_cards, '4.5.3', platform_name=platform_name)

        assert [card.note for card in inv.cards] == [expected_note] * 2

    def test_failed_check_is_not_an_empty_list(self):
        assert BLENDER_CHECK_FAILED != []
        assert BLENDER_CHECK_FAILED is not None
        assert repr(BLENDER_CHECK_FAILED) == 'BLENDER_CHECK_FAILED'


class TestMacosBlenderStates:

    def test_failed_check_keeps_apple_gpu_with_the_note(self, caplog):
        caplog.set_level(logging.DEBUG)

        inv = inventory.build((_apple(),), BLENDER_CHECK_FAILED, None, platform_name='darwin')

        assert [(card.key, card.name, card.blender, card.note) for card in inv.cards] == [
            (APPLE_GPU_KEY, 'Apple M2 Max', None, FAILED_NOTE)]
        assert inv.os_probe_failed is False
        assert records(caplog, LOGGER) == []

    def test_both_probes_failed_gives_an_empty_failed_inventory(self):
        inv = inventory.build(None, BLENDER_CHECK_FAILED, None, platform_name='darwin')

        assert inv.cards == ()
        assert inv.os_probe_failed is True

    def test_failed_check_with_two_gpus_still_offers_no_card(self):
        inv = inventory.build((_apple('Intel UHD'), _apple('AMD Radeon Pro')), BLENDER_CHECK_FAILED, None,
                              platform_name='darwin')

        assert inv.cards == ()

    @pytest.mark.parametrize('blender_cards, expected', [
        (None, [(APPLE_GPU_KEY, 'Blender not installed yet')]),
        ([], []),                                    # a real empty list: no Metal device, no card
        (BLENDER_CHECK_FAILED, [(APPLE_GPU_KEY, FAILED_NOTE)]),
    ])
    def test_each_state_keeps_its_own_outcome(self, blender_cards, expected):
        inv = inventory.build((_apple(),), blender_cards, None, platform_name='darwin')

        assert [(card.key, card.note) for card in inv.cards] == expected


class TestRevisionId:

    def test_revision_is_passed_through_from_the_os_card(self, platform_name):
        inv = inventory.build(two_xt_os_cards(), two_xt_blender_cards(), '4.5.3', platform_name=platform_name)

        assert [card.revision_id for card in inv.cards] == [0xC8, 0xCC]

    @pytest.mark.parametrize('blender_cards', [None, BLENDER_CHECK_FAILED, []])
    def test_revision_is_kept_whatever_blender_reports(self, platform_name, blender_cards):
        inv = inventory.build(two_xt_os_cards(), blender_cards, '4.5.3', platform_name=platform_name)

        assert [card.revision_id for card in inv.cards] == [0xC8, 0xCC]

    def test_missing_revision_stays_none(self, platform_name):
        inv = inventory.build((OsCard(key=KEY_XT_03, name='x'),), [], '4.5.3', platform_name=platform_name)

        assert inv.cards[0].revision_id is None

    def test_blender_fallback_has_no_revision(self, platform_name):
        inv = inventory.build(None, [{'id': RX_7900_XT_03, 'type': 'HIP', 'name': 'x'}], '4.5.3',
                              platform_name=platform_name)

        assert [card.revision_id for card in inv.cards] == [None]

    def test_macos_card_has_no_revision(self):
        inv = inventory.build((_apple(),), [{'id': 'METAL_Apple M2 Max', 'type': 'METAL', 'name': 'x'}], '4.5.3',
                              platform_name='darwin')

        assert inv.cards[0].revision_id is None
