# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the screen warning ``ticked_drives_screen`` (FR-CS-3, AC-37, D21).

The exact texts, the main-screen variant, the 200-character fallback to
``PCI <address>``, no warning for an unticked card or an unknown mark, the
order after ``engine_not_tied``, the log line once per appearance at a
check, and the warning and marks as the card GET and the heartbeat report
carry them.
"""
import dataclasses
import logging

import pytest

from sethlans_worker_agent.cards import matching, payload
from sethlans_worker_agent.cards import warnings as card_warnings
from sethlans_worker_agent.cards.identity import APPLE_GPU_KEY
from sethlans_worker_agent.cards.inventory import Card, Inventory
from sethlans_worker_agent.web_ui import cards_view
from tests.unit.worker._card_choice import KEY_XT_03, KEY_XT_23, inv, section, xt_03, xt_23

LOGGER = 'sethlans_worker_agent.cards.warnings'
SCREEN = 'AMD Radeon RX 7900 XT (PCI 03:00.0) is ticked and drives a screen; rendering can slow it.'
MAIN = 'AMD Radeon RX 7900 XT (PCI 23:00.0) is ticked and drives the main screen; rendering can slow it.'


def _marked(card, screen, primary=None):
    return dataclasses.replace(card, screen=screen, primary_screen=primary)


def _warnings(inventory, ticked, method='gpu'):
    return card_warnings.build(inventory, matching.evaluate(section(method, 'split', ticked, inventory), inventory))


def _screen_only(found):
    return [warning for warning in found if warning['code'] == card_warnings.SCREEN_CODE]


class TestTexts:

    def test_ticked_card_driving_a_screen(self):
        found = _warnings(inv(_marked(xt_03(), True, False), xt_23()), [KEY_XT_03])

        assert _screen_only(found) == [{'code': 'ticked_drives_screen', 'key': KEY_XT_03, 'text': SCREEN}]

    def test_ticked_card_driving_the_main_screen(self):
        found = _warnings(inv(xt_03(), _marked(xt_23(), True, True)), [KEY_XT_03, KEY_XT_23])

        assert found == [{'code': 'ticked_drives_screen', 'key': KEY_XT_23, 'text': MAIN}]

    def test_linux_unknown_primary_is_the_plain_text(self):
        found = _warnings(inv(_marked(xt_03(), True, None)), [KEY_XT_03])

        assert [warning['text'] for warning in found] == [SCREEN]

    @pytest.mark.parametrize('screen, primary', [(None, None), (False, False), (False, None), (None, True)])
    def test_no_warning_without_a_known_screen(self, screen, primary):
        assert _warnings(inv(_marked(xt_03(), screen, primary)), [KEY_XT_03]) == []

    def test_untick_gives_no_warning(self):
        cards = inv(_marked(xt_03(), True, True), xt_23())

        assert _screen_only(_warnings(cards, [KEY_XT_23])) == []

    def test_one_per_ticked_card_in_pci_order_after_the_engine_warning(self):
        cards = inv(_marked(xt_03(), True, False), _marked(xt_23(), True, True))

        found = _warnings(cards, [KEY_XT_03, KEY_XT_23], method='cpu')

        assert [(warning['code'], warning['key']) for warning in found] == [
            ('engine_not_tied', None), ('ticked_drives_screen', KEY_XT_03), ('ticked_drives_screen', KEY_XT_23)]

    def test_any_state_of_a_ticked_card_counts(self):
        cards = inv(_marked(xt_03(), True, False))
        alerts = [{'id': 'c' * 32, 'kind': 'card_lost', 'key': KEY_XT_03, 'name': 'x'}]
        result = matching.evaluate(section('gpu', 'split', [KEY_XT_03], cards, alerts=alerts), cards)

        assert result.states[KEY_XT_03] == 'held'
        assert _screen_only(card_warnings.build(cards, result))[0]['key'] == KEY_XT_03

    def test_failed_evaluation_gives_no_screen_warning(self):
        cards = inv(_marked(xt_03(), True, True))

        assert card_warnings.build(cards, matching.evaluate(section('gpu', 'split', [KEY_XT_03], cards), None)) == []

    def test_apple_gpu_has_no_pci_part(self):
        card = Card(key=APPLE_GPU_KEY, name='Apple M2 Max', screen=True, primary_screen=True)

        assert card_warnings.screen_text(card) == (
            'Apple M2 Max is ticked and drives the main screen; rendering can slow it.')


class TestFallback:
    """Over 200 characters, ``<name> (PCI …)`` becomes ``PCI <address>`` (the manager's cap)."""

    @staticmethod
    def _named(length, primary):
        return Card(key=KEY_XT_23, name='N' * length, screen=True, primary_screen=primary)

    @pytest.mark.parametrize('primary', [True, False])
    def test_exactly_200_keeps_the_name(self, primary):
        tail = len(card_warnings.MAIN_SCREEN_TEXT if primary else card_warnings.SCREEN_TEXT) - len('{card}')
        length = 200 - tail - len(' (PCI 23:00.0)')

        text = card_warnings.screen_text(self._named(length, primary))

        assert len(text) == 200 and text.startswith('N' * length + ' (PCI 23:00.0) is ticked')

    @pytest.mark.parametrize('primary, expected', [
        (True, 'PCI 0000:23:00.0 is ticked and drives the main screen; rendering can slow it.'),
        (False, 'PCI 0000:23:00.0 is ticked and drives a screen; rendering can slow it.'),
    ])
    def test_201_falls_back_to_the_address(self, primary, expected):
        tail = len(card_warnings.MAIN_SCREEN_TEXT if primary else card_warnings.SCREEN_TEXT) - len('{card}')
        length = 201 - tail - len(' (PCI 23:00.0)')

        assert card_warnings.screen_text(self._named(length, primary)) == expected

    def test_longest_os_name_on_the_main_screen_falls_back(self):
        assert card_warnings.screen_text(self._named(128, True)).startswith('PCI 0000:23:00.0 ')

    def test_braces_in_a_name_are_kept_as_text(self):
        card = Card(key=KEY_XT_03, name='GPU {card} {0}', screen=True, primary_screen=False)

        assert card_warnings.screen_text(card).startswith('GPU {card} {0} (PCI 03:00.0) is ticked')


class TestLog:

    def _lines(self, caplog):
        return [r.getMessage() for r in caplog.records if r.name == LOGGER]

    def test_logged_once_while_it_lasts_and_again_after_it_went_away(self, caplog):
        caplog.set_level(logging.WARNING, logger=LOGGER)
        driving = inv(_marked(xt_03(), True, False))
        sect = section('gpu', 'split', [KEY_XT_03], driving)
        quiet = inv(_marked(xt_03(), False, False))

        for cards in (driving, driving, quiet, driving):
            card_warnings.log_screen_warnings(cards, matching.evaluate(sect, cards))

        assert self._lines(caplog) == [SCREEN, SCREEN]
        assert all(r.levelno == logging.WARNING for r in caplog.records if r.name == LOGGER)

    def test_failed_evaluation_changes_nothing(self, caplog):
        caplog.set_level(logging.WARNING, logger=LOGGER)
        driving = inv(_marked(xt_03(), True, False))
        sect = section('gpu', 'split', [KEY_XT_03], driving)

        card_warnings.log_screen_warnings(driving, matching.evaluate(sect, driving))
        card_warnings.log_screen_warnings(driving, matching.evaluate(sect, None))
        card_warnings.log_screen_warnings(driving, matching.evaluate(sect, driving))

        assert self._lines(caplog) == [SCREEN]

    def test_non_ascii_name_is_escaped_in_the_log(self, caplog):
        caplog.set_level(logging.WARNING, logger=LOGGER)
        cards = inv(dataclasses.replace(xt_03(), name='Radeon é', screen=True, primary_screen=False))
        sect = section('gpu', 'split', [KEY_XT_03], cards)

        card_warnings.log_screen_warnings(cards, matching.evaluate(sect, cards))

        assert self._lines(caplog) == [
            'Radeon \\xe9 (PCI 03:00.0) is ticked and drives a screen; rendering can slow it.']


class TestGetAndReport:
    """The card GET and the heartbeat ``cards`` object carry the marks and the warning."""

    def test_get_and_payload(self):
        cards = inv(_marked(xt_03(), True, False), _marked(xt_23(), None, None))
        sect = section('gpu', 'split', [KEY_XT_03, KEY_XT_23], cards)

        answer = cards_view.view(cards_view.Probe(cards, None), sect)
        report = payload.shape(answer, sect, False)

        assert [(card['key'], card['screen'], card['primary_screen']) for card in answer['cards']] == [
            (KEY_XT_03, True, False), (KEY_XT_23, None, None)]
        assert [(card['key'], card['screen'], card['primary_screen']) for card in report['cards']] == [
            (KEY_XT_03, True, False), (KEY_XT_23, None, None)]
        expected = [{'code': 'ticked_drives_screen', 'key': KEY_XT_03, 'text': SCREEN}]
        assert answer['warnings'] == expected
        assert report['warnings'] == expected

    def test_empty_inventory_has_no_marks_or_warnings(self):
        sect = section('cpu', 'split', [], inv())

        answer = cards_view.view(cards_view.Probe(Inventory(), None), sect)

        assert answer['cards'] == [] and answer['warnings'] == []
