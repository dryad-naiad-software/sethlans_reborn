# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Screen marks at the card checks and on the card pages (FR-CK-1, FR-CS-2, FR-CS-3, AC-37).

The start check, the periodic check, the light check during an Unreal hold
and the card pages' probe each read the marks afresh; a check logs a new
screen warning once; marks change no slot, no state and request no rebuild.
``screens._platform_marks`` is the fake screen; ``CardEnv`` fakes the rest.
"""
import logging
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import job_processor, system_monitor
from sethlans_worker_agent.capacity import card_check, rebuild
from sethlans_worker_agent.cards import screens
from sethlans_worker_agent.web_ui import cards_view
from tests.unit.worker._card_check_env import CardEnv
from tests.unit.worker.executors._fakes import fake_cards, key

WARNINGS = 'sethlans_worker_agent.cards.warnings'
MAIN = 'is ticked and drives the main screen; rendering can slow it.'


@pytest.fixture
def machine(monkeypatch, mocker):
    mocker.patch.object(system_monitor, 'get_gpu_device_details', return_value=[])
    mocker.patch.object(system_monitor, 'get_cpu_thread_count', return_value=8)
    card_check.reset_for_tests()
    yield CardEnv(monkeypatch, count=2)
    card_check.reset_for_tests()


@pytest.fixture
def screen(mocker):
    """The fake screen read: ``marks`` per key; ``calls`` lists the keys asked for at each read."""
    fake = SimpleNamespace(marks={}, calls=[])

    def platform_marks(platform_name, inventory):
        fake.calls.append(tuple(card.key for card in inventory.cards))
        return dict(fake.marks)

    mocker.patch.object(screens, '_platform_marks', side_effect=platform_marks)
    return fake


def _screen_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == WARNINGS and 'drives' in r.getMessage()]


class TestChecks:

    def test_start_check_reads_the_marks_and_logs_the_warning_once(self, machine, screen, caplog):
        screen.marks[key(0)] = (True, True)

        with caplog.at_level(logging.WARNING, logger=WARNINGS):
            cap = job_processor.init_capacity()
            assert card_check.check(cap) is None

        assert len(screen.calls) == 2                      # the start check and the periodic check
        lines = _screen_lines(caplog)
        assert len(lines) == 1 and lines[0].endswith(MAIN) and '(PCI ' in lines[0]
        assert cap.profile.cards == fake_cards(2) and cap.profile.gpu_slot_count == 2
        assert rebuild.pending() is False

    def test_cable_moved_at_a_periodic_check_logs_and_changes_no_slot(self, machine, screen, caplog):
        cap = job_processor.init_capacity()
        profile = cap.profile
        screen.marks[key(1)] = (True, False)

        with caplog.at_level(logging.WARNING, logger=WARNINGS):
            card_check.check(cap)

        assert [line.endswith('is ticked and drives a screen; rendering can slow it.')
                for line in _screen_lines(caplog)] == [True]
        assert cap.profile == profile and rebuild.pending() is False
        assert machine.alerts() == []

    def test_unticked_card_on_a_screen_logs_nothing(self, machine, screen, caplog):
        machine.compute['cards'] = [machine.record(0)]
        screen.marks[key(1)] = (True, True)

        with caplog.at_level(logging.WARNING, logger=WARNINGS):
            job_processor.init_capacity()

        assert _screen_lines(caplog) == []

    def test_light_check_during_an_unreal_hold_reads_the_marks(self, machine, screen, caplog):
        cap = job_processor.init_capacity()
        screen.calls.clear()
        screen.marks[key(0)] = (True, True)

        with caplog.at_level(logging.WARNING, logger=WARNINGS):
            card_check.check_during_unreal(cap, 1000.0)

        assert screen.calls == [(key(0), key(1))]
        assert len(_screen_lines(caplog)) == 1
        assert machine.detections == 0                     # no Blender during the hold

    def test_failed_screen_read_is_unknown_and_changes_nothing(self, machine, mocker, caplog):
        mocker.patch.object(screens, '_platform_marks', side_effect=RuntimeError('dxgi broke'))

        cap = job_processor.init_capacity()

        assert cap.profile.cards == fake_cards(2) and machine.alerts() == []


class TestCardPages:

    def test_probe_now_carries_the_marks_and_the_get_the_warning(self, machine, screen):
        from sethlans_worker_agent.cards import store
        screen.marks[key(0)] = (True, False)
        screen.marks[key(1)] = (False, False)

        probe = cards_view.probe_now()
        answer = cards_view.view(probe, store.snapshot())

        assert [(card['key'], card['screen'], card['primary_screen']) for card in answer['cards']] == [
            (key(0), True, False), (key(1), False, False)]
        assert [(w['code'], w['key']) for w in answer['warnings']] == [('ticked_drives_screen', key(0))]
