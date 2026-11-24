# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A ticked card that only waits for Blender to be installed (#305).

Before Blender is downloaded the inventory notes every card
``inventory.NOTE_NO_BLENDER``. A ticked card in state ``ok`` with that note
is logged once at INFO ("waiting for Blender"), not at ERROR. A card that
Blender, once installed, does not offer is still an ERROR. Neither card gets
a slot. When every ticked card only waits, the problem is
``WAITING_FOR_BLENDER_TEXT``, logged at INFO; a waiting card beside a lost
or held one keeps ``NONE_USABLE_TEXT`` (WARNING). The periodic card check
finds the waiting cards once Blender is in place and the rebuild gives them
their slots.
"""
import logging

import pytest

from sethlans_worker_agent import config_compute, job_processor, system_monitor
from sethlans_worker_agent.capacity import rebuild
from sethlans_worker_agent.cards import in_use, inventory, matching, startup_log
from sethlans_worker_agent.cards.in_use import NONE_USABLE_TEXT, WAITING_FOR_BLENDER_TEXT, blender_cards_in_use
from tests.unit.worker._card_check_env import CardEnv
from tests.unit.worker._card_choice import select, section
from tests.unit.worker.cards._card_ids import (
    KEY_XT_03, KEY_XT_23, RX_7900_XT_23, blender, records, two_xt_os_cards,
)
from tests.unit.worker.executors._fakes import fake_cards

LOGGER = 'sethlans_worker_agent.cards.in_use'
PROFILE_LOGGER = 'sethlans_worker_agent.capacity.card_profile'
WAITING_03 = ("Ticked graphics card 'AMD Radeon RX 7900 XT' (pci:0000:03:00.0) is waiting for Blender: Blender is "
              "not installed yet. The card is checked again at the first card check after Blender is installed.")


def _no_blender_yet():
    return inventory.build(two_xt_os_cards(), None, None, platform_name='linux')


def _blender_without_card_03():
    """Blender 4.5.0 is installed and offers only the card at 23:00."""
    return inventory.build(two_xt_os_cards(), [blender(RX_7900_XT_23, 'HIP', 'AMD Radeon RX 7900 XT')], '4.5.0',
                           platform_name='linux')


def _ticked(inv, method='gpu'):
    return section(method, 'split', [KEY_XT_03], inv)


class TestWaitingForBlender:

    def test_inventory_notes_the_card_as_waiting(self):
        card = _no_blender_yet().cards[0]
        assert (card.key, card.blender, card.note) == (KEY_XT_03, None, inventory.NOTE_NO_BLENDER)

    def test_waiting_card_is_logged_once_at_info_and_gets_no_slot(self, caplog):
        caplog.set_level(logging.DEBUG, logger=LOGGER)
        inv = _no_blender_yet()

        assert blender_cards_in_use(inv, _ticked(inv)) == ()
        assert blender_cards_in_use(inv, _ticked(inv)) == ()

        assert [r.getMessage() for r in records(caplog, LOGGER, 'INFO')] == [WAITING_03]
        assert records(caplog, LOGGER, 'ERROR') == []

    def test_every_waiting_card_has_its_own_line(self, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER)
        inv = _no_blender_yet()

        blender_cards_in_use(inv, section('cpu_gpu', 'split', [KEY_XT_23, KEY_XT_03], inv))

        lines = [r.getMessage() for r in records(caplog, LOGGER, 'INFO')]
        assert lines == [WAITING_03, WAITING_03.replace('03:00.0', '23:00.0')]

    def test_method_cpu_logs_nothing(self, caplog):
        caplog.set_level(logging.DEBUG, logger=LOGGER)
        inv = _no_blender_yet()
        assert blender_cards_in_use(inv, _ticked(inv, method='cpu')) == ()
        assert records(caplog, LOGGER) == []

    def test_missing_card_without_blender_is_still_an_error(self, caplog):
        """Only a card in state ``ok`` waits; one missing from the OS list is a fault whatever Blender's state."""
        caplog.set_level(logging.DEBUG, logger=LOGGER)
        sect = _ticked(_no_blender_yet())
        only_23 = inventory.build(two_xt_os_cards()[1:], None, None, platform_name='linux')

        assert blender_cards_in_use(only_23, sect) == ()

        assert records(caplog, LOGGER, 'INFO') == []
        assert [r.getMessage() for r in records(caplog, LOGGER, 'ERROR')] == [
            "Ticked graphics card 'AMD Radeon RX 7900 XT' (pci:0000:03:00.0) is not used by Blender: "
            "missing from the card list."]


class TestBlenderDoesNotOffer:

    def test_installed_blender_that_does_not_offer_the_card_is_an_error(self, caplog):
        caplog.set_level(logging.DEBUG, logger=LOGGER)
        inv = _blender_without_card_03()

        assert blender_cards_in_use(inv, _ticked(inv)) == ()

        assert records(caplog, LOGGER, 'INFO') == []
        assert [r.getMessage() for r in records(caplog, LOGGER, 'ERROR')] == [
            "Ticked graphics card 'AMD Radeon RX 7900 XT' (pci:0000:03:00.0) is not used by Blender: "
            "not offered by Blender (not offered by Blender 4.5.0)."]

    def test_failed_blender_device_check_is_an_error(self, caplog):
        caplog.set_level(logging.DEBUG, logger=LOGGER)
        inv = inventory.build(two_xt_os_cards(), inventory.BLENDER_CHECK_FAILED, None, platform_name='linux')

        assert blender_cards_in_use(inv, _ticked(inv)) == ()

        assert records(caplog, LOGGER, 'INFO') == []
        assert [r.getMessage() for r in records(caplog, LOGGER, 'ERROR')] == [
            "Ticked graphics card 'AMD Radeon RX 7900 XT' (pci:0000:03:00.0) is not used by Blender: "
            f"not offered by Blender ({inventory.NOTE_BLENDER_FAILED})."]


class TestSlotOutcome:

    def test_waiting_card_gets_no_slot_and_the_waiting_problem(self):
        inv = _no_blender_yet()
        assert select(_ticked(inv), inv) == in_use.Selection(problem=WAITING_FOR_BLENDER_TEXT)

    def test_not_offered_card_gets_no_slot_and_the_none_usable_problem(self):
        inv = _blender_without_card_03()
        assert select(_ticked(inv), inv) == in_use.Selection(problem=NONE_USABLE_TEXT)

    def test_waiting_beside_a_lost_card_keeps_none_usable(self):
        sect = section('gpu', 'split', [KEY_XT_03, KEY_XT_23], _no_blender_yet())
        only_03 = inventory.build(two_xt_os_cards()[:1], None, None, platform_name='linux')
        assert select(sect, only_03) == in_use.Selection(problem=NONE_USABLE_TEXT)

    def test_waiting_beside_a_held_card_keeps_none_usable(self):
        inv = _no_blender_yet()
        alert = {'id': 'c' * 32, 'kind': 'card_lost', 'key': KEY_XT_23, 'name': 'x'}
        sect = section('gpu', 'split', [KEY_XT_03, KEY_XT_23], inv, alerts=[alert])
        assert select(sect, inv).problem == NONE_USABLE_TEXT

    def test_every_ticked_card_waiting_is_the_waiting_problem(self):
        inv = _no_blender_yet()
        sect = section('cpu_gpu', 'split', [KEY_XT_03, KEY_XT_23], inv)
        assert select(sect, inv).problem == WAITING_FOR_BLENDER_TEXT


class TestProblemMessage:

    def test_waiting_only_when_cards_are_ticked_and_none_is_in_use(self):
        inv = _no_blender_yet()
        assert in_use.problem_message(_ticked(inv), (), waiting=True) == WAITING_FOR_BLENDER_TEXT
        assert in_use.problem_message(_ticked(inv), (), waiting=False) == NONE_USABLE_TEXT
        assert in_use.problem_message(_ticked(inv, method='cpu'), (), waiting=True) == ''
        assert in_use.problem_message(config_compute.parse({'method': 'gpu'}, {}), (), waiting=True) == \
            in_use.NO_TICK_TEXT

    def test_waiting_for_blender_needs_every_ticked_card_ok_and_waiting(self):
        waiting, offered = _no_blender_yet(), _blender_without_card_03()
        sect = _ticked(waiting)
        ok = {KEY_XT_03: matching.OK}
        assert in_use.waiting_for_blender(waiting, sect, ok) is True
        assert in_use.waiting_for_blender(offered, sect, ok) is False
        assert in_use.waiting_for_blender(waiting, sect, {KEY_XT_03: matching.HELD}) is False
        assert in_use.waiting_for_blender(inventory.Inventory(), sect, ok) is False
        assert in_use.waiting_for_blender(waiting, config_compute.parse({'method': 'gpu'}, {}), {}) is False


class TestStartThenBlenderInstalled:

    @pytest.fixture
    def machine(self, monkeypatch, mocker):
        """Two ticked cards, method gpu, no Blender downloaded yet."""
        mocker.patch.object(system_monitor, 'get_gpu_device_details', return_value=[])
        mocker.patch.object(system_monitor, 'get_cpu_thread_count', return_value=8)
        env = CardEnv(monkeypatch, count=2, method='gpu')
        monkeypatch.setattr(startup_log, 'cached_blender_list', lambda: (None, None))
        return env

    def test_waiting_cards_get_their_slots_after_the_first_check_with_blender(self, machine, caplog):
        caplog.set_level(logging.DEBUG)

        cap = job_processor.init_capacity()

        assert (cap.profile.gpu_slot_count, cap.profile.cards, cap.profile.problem) == \
            (0, (), WAITING_FOR_BLENDER_TEXT)
        assert [r.getMessage() for r in records(caplog, PROFILE_LOGGER, 'INFO')].count(WAITING_FOR_BLENDER_TEXT) == 1
        assert records(caplog, PROFILE_LOGGER, 'WARNING') == []
        assert len(records(caplog, LOGGER, 'INFO')) == 2 and records(caplog, LOGGER, 'ERROR') == []

        assert cap.assert_gpu_list_unchanged() is None  # Blender is in place: the detection lists the cards
        assert machine.detections == 1 and rebuild.pending() is True
        assert rebuild.step(cap) == rebuild.SWAPPED
        assert cap.profile.cards == fake_cards(2) and cap.profile.problem == ''
        assert records(caplog, LOGGER, 'ERROR') == []

    def test_waiting_beside_a_missing_card_is_a_warning(self, machine, caplog):
        machine.lose(1)
        caplog.set_level(logging.DEBUG)

        cap = job_processor.init_capacity()

        assert cap.profile.problem == NONE_USABLE_TEXT
        assert [r.getMessage() for r in records(caplog, PROFILE_LOGGER, 'WARNING')] == [NONE_USABLE_TEXT]
        assert WAITING_FOR_BLENDER_TEXT not in [r.getMessage() for r in records(caplog, PROFILE_LOGGER)]


def test_saved_record_without_a_name_is_named_by_its_key(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    inv = _no_blender_yet()
    sect = config_compute.parse({'method': 'gpu', 'cards': [dict(in_use.record_of(inv.cards[0]), name='')]}, {})

    assert blender_cards_in_use(inv, sect) == ()

    assert [r.getMessage() for r in records(caplog, LOGGER, 'INFO')] == [
        WAITING_03.replace("'AMD Radeon RX 7900 XT'", "'pci:0000:03:00.0'")]
