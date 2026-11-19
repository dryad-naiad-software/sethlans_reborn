# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``cards.screens`` (FR-CS-1, FR-CS-2, AC-36, AC-37).

``read`` picks the platform module, never raises and gives ``(None, None)``
for every card on any failure; macOS reuses the card probe's
``system_profiler`` output; ``marked`` puts the marks on the inventory and
changes no tick, slot or state. The root conftest makes every mark unknown;
these tests put the real ``_platform_marks`` back where they need it.
"""
import dataclasses
import logging

import pytest

from sethlans_worker_agent.cards import inventory, matching, screens, screens_linux, screens_windows
from sethlans_worker_agent.cards.identity import APPLE_GPU_KEY
from sethlans_worker_agent.cards.inventory import Card, Inventory
from sethlans_worker_agent.cards.os_probe import OsCard
from tests.unit.worker._card_choice import KEY_XT_03, KEY_XT_23, capacity_for, inv, section, xt_03, xt_23

REAL_PLATFORM_MARKS = screens._platform_marks
LOGGER = 'sethlans_worker_agent.cards.screens'


@pytest.fixture
def real_platform(mocker):
    mocker.patch.object(screens, '_platform_marks', REAL_PLATFORM_MARKS)


def _two():
    return inv(xt_03(), xt_23())


class TestRead:

    def test_windows_marks_are_given_per_card_and_missing_cards_are_unknown(self, mocker, real_platform):
        mocker.patch.object(screens_windows, 'read', return_value={KEY_XT_03: (True, True), 'pci:0000:99:00.0': (1, 2)})

        assert screens.read(_two(), 'win32') == {KEY_XT_03: (True, True), KEY_XT_23: (None, None)}

    def test_linux_dispatch(self, mocker, real_platform):
        mocker.patch.object(screens_linux, 'read', return_value={KEY_XT_23: (True, None)})

        assert screens.read(_two(), 'linux') == {KEY_XT_03: (None, None), KEY_XT_23: (True, None)}

    @pytest.mark.parametrize('found, expected', [
        ((True, None), (True, None)), ((False, False), (False, False)), ((None, True), (None, None)),
        ((1, True), (None, None)), ((True, 'yes'), (True, None)), ((True,), (None, None)), ([True, True], (None, None)),
        (None, (None, None)),
    ])
    def test_malformed_marks_are_unknown(self, mocker, real_platform, found, expected):
        mocker.patch.object(screens_windows, 'read', return_value={KEY_XT_03: found})

        assert screens.read(inv(xt_03()), 'win32')[KEY_XT_03] == expected

    def test_failed_read_is_unknown_for_every_card(self, mocker, real_platform):
        mocker.patch.object(screens_windows, 'read', return_value=None)

        assert screens.read(_two(), 'win32') == {KEY_XT_03: (None, None), KEY_XT_23: (None, None)}

    def test_an_exception_never_escapes_and_is_logged_once(self, mocker, real_platform, caplog):
        mocker.patch.object(screens_windows, 'read', side_effect=OSError('user32.dll missing'))
        caplog.set_level(logging.WARNING, logger=LOGGER)

        assert screens.read(_two(), 'win32') == {KEY_XT_03: (None, None), KEY_XT_23: (None, None)}
        assert screens.read(_two(), 'win32') == {KEY_XT_03: (None, None), KEY_XT_23: (None, None)}
        assert [r.levelno for r in caplog.records if r.name == LOGGER] == [logging.WARNING]

    def test_unknown_platform_is_unknown(self, real_platform):
        assert screens.read(_two(), 'sunos5') == {KEY_XT_03: (None, None), KEY_XT_23: (None, None)}


class TestMacos:
    """FR-CS-1 macOS: the marks come from the FR-CI-4 output; ``system_profiler`` is not run again."""

    @staticmethod
    def _apple(display):
        os_cards = (OsCard(key=None, name='Apple M2 Max', display=display),)
        blender = [{'id': 'METAL_Apple M2 Max', 'type': 'METAL', 'name': 'Apple M2 Max'}]
        return inventory.build(os_cards, blender, '4.5.1', platform_name='darwin')

    @pytest.mark.parametrize('display', [True, False])
    def test_display_under_the_gpu_is_screen_and_main_screen(self, mocker, real_platform, display):
        run = mocker.patch('sethlans_worker_agent.cards.os_probe_macos._run')
        built = self._apple(display)

        assert (built.cards[0].screen, built.cards[0].primary_screen) == (display, display)
        assert screens.read(built, 'darwin') == {APPLE_GPU_KEY: (display, display)}
        run.assert_not_called()

    def test_without_blender_the_marks_are_still_set(self):
        built = inventory.build((OsCard(key=None, name='Apple M2', display=True),), None, platform_name='darwin')

        assert (built.cards[0].screen, built.cards[0].primary_screen) == (True, True)


class TestMarked:

    def test_marks_are_put_on_the_cards(self, mocker, real_platform):
        mocker.patch.object(screens_windows, 'read', return_value={KEY_XT_03: (True, False), KEY_XT_23: (False, False)})

        marked = screens.marked(_two(), 'win32')

        assert [(card.key, card.screen, card.primary_screen) for card in marked.cards] == [
            (KEY_XT_03, True, False), (KEY_XT_23, False, False)]
        assert dataclasses.replace(marked.cards[0], screen=None, primary_screen=None) == xt_03()

    def test_empty_inventory_reads_nothing(self, mocker, real_platform):
        read = mocker.patch.object(screens_windows, 'read')

        assert screens.marked(Inventory(), 'win32') == Inventory()
        read.assert_not_called()

    def test_root_conftest_makes_every_mark_unknown(self):
        marked = screens.marked(inv(dataclasses.replace(xt_03(), screen=True, primary_screen=True)))

        assert (marked.cards[0].screen, marked.cards[0].primary_screen) == (None, None)


class TestMarksChangeNothing:
    """O10, FR-CS-2: marks never change ticks, slots or states (AC-37)."""

    @pytest.mark.parametrize('method, gpu_mode, ticked', [
        ('gpu', 'split', [KEY_XT_03]), ('cpu_gpu', 'combined', [KEY_XT_03, KEY_XT_23]), ('cpu', 'split', [KEY_XT_23]),
    ])
    def test_states_cards_in_use_and_slots_are_the_same(self, method, gpu_mode, ticked):
        plain = _two()
        marked = Inventory(cards=tuple(dataclasses.replace(card, screen=True, primary_screen=True)
                                       for card in plain.cards), blender_version=plain.blender_version)
        sect = section(method, gpu_mode, ticked, plain)

        before, after = matching.evaluate(sect, plain), matching.evaluate(sect, marked)

        assert after.states == before.states
        assert after.blender_cards == before.blender_cards
        assert [card.key for card in after.unreal_cards] == [card.key for card in before.unreal_cards]
        assert (after.problem, after.unusable, after.notices) == (before.problem, before.unusable, before.notices)
        assert capacity_for(sect, marked).profile == capacity_for(sect, plain).profile
        assert sect.ticked_keys == tuple(ticked)


def test_card_defaults_are_unknown():
    card = Card(key=KEY_XT_03, name='x')
    assert (card.screen, card.primary_screen) == (None, None)
