# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The "cards found but none chosen" hint (#305).

With method ``cpu``, no ticked card and at least one graphics card found,
the profile log adds one INFO line saying how to choose a card: at start
(``job_processor.init_capacity``, the cards of the startup card list) and
after a rebuild (``rebuild.step``, the cards of the card pages' cached probe;
never a new probe). Not when a card is ticked, when no card is found, or when
the settings could not be read; never on the periodic check. The hint is log
data only: the slots are those of a profile without it.
"""
import dataclasses
import logging
import time

import pytest

from sethlans_worker_agent import config_compute, job_processor, system_monitor
from sethlans_worker_agent.capacity import card_profile, rebuild
from sethlans_worker_agent.cards import in_use, startup_log
from sethlans_worker_agent.cards.inventory import Card, Inventory
from sethlans_worker_agent.web_ui import cards_cache, cards_view
from tests.unit.worker._card_check_env import CardEnv

LOGGER = 'sethlans_worker_agent.capacity.card_profile'
HINT = ("Graphics cards found but none chosen; rendering on the CPU only. Choose GPU (or CPU and GPU) and tick a "
        "card in the worker settings or from the manager, or set SETHLANS_COMPUTE_METHOD=gpu and "
        "SETHLANS_COMPUTE_CARDS.")
REBUILT = 'The slots were rebuilt from the graphics card choice.'


@pytest.fixture
def quiet_host(mocker):
    mocker.patch.object(system_monitor, 'get_gpu_device_details', return_value=[])
    mocker.patch.object(system_monitor, 'get_cpu_thread_count', return_value=8)


@pytest.fixture
def machine(monkeypatch, quiet_host):
    """Two cards found, none ticked, method ``cpu``; the startup card list runs on this fake machine."""
    env = CardEnv(monkeypatch, count=2, ticked=[], method='cpu')
    startup_log.reset_for_tests()
    return env


def _hints(caplog):
    return [r for r in caplog.records if r.name == LOGGER and r.getMessage() == HINT]


def _messages(caplog):
    return [r.getMessage() for r in caplog.records if r.name == LOGGER]


def _cache_probe(monkeypatch, count):
    """The card pages' cached probe, listing ``count`` cards."""
    cards = tuple(Card(key=f'pci:0000:{i + 1:02x}:00.0', name=f'GPU {i}') for i in range(count))
    monkeypatch.setattr(cards_cache, '_cache', (time.monotonic(), cards_cache._blender_token(),
                                                cards_view.Probe(Inventory(cards=cards), None)))


class TestHintText:

    def test_text_names_the_settings_and_both_variables(self):
        assert card_profile.CPU_ONLY_HINT_TEXT == HINT


class TestProfileFor:

    @pytest.fixture(autouse=True)
    def _host(self, quiet_host):
        pass

    @pytest.mark.parametrize('raw, detected, expected', [
        ({'method': 'cpu'}, 1, True),
        ({'method': 'cpu'}, 3, True),
        ({'method': 'cpu'}, 0, False),
        ({'method': 'gpu'}, 2, False),
        ({'method': 'cpu_gpu'}, 2, False),
        ({'method': 'cpu', 'cards': [{'key': 'pci:0000:01:00.0'}]}, 2, False),  # ticked for Unreal
    ])
    def test_hint_only_for_cpu_without_ticks_and_with_cards(self, raw, detected, expected):
        section = config_compute.parse(raw, {})
        profile = card_profile.profile_for(section, in_use.Selection(), detected=detected)
        assert profile.cpu_only_hint is expected

    def test_default_detected_is_no_hint(self):
        assert card_profile.profile_for(config_compute.parse({}, {}), in_use.Selection()).cpu_only_hint is False

    def test_unreadable_settings_give_no_hint(self):
        section = dataclasses.replace(config_compute.parse(None, {}), read_failed=True)
        assert card_profile.profile_for(section, in_use.Selection(), detected=2).cpu_only_hint is False

    def test_hint_changes_no_slot(self):
        section = config_compute.parse({'method': 'cpu'}, {})
        without = card_profile.profile_for(section, in_use.Selection(), detected=0)
        hinted = card_profile.profile_for(section, in_use.Selection(), detected=2)
        assert dataclasses.replace(hinted, cpu_only_hint=False) == without
        assert (hinted.gpu_slot_count, hinted.cpu_slot_count, hinted.cards) == (0, 1, ())


class TestDetectedCardCount:

    def test_counts_the_given_inventory(self):
        cards = (Card(key='pci:0000:01:00.0', name='a'), Card(key='pci:0000:02:00.0', name='b'))
        assert card_profile.detected_card_count(Inventory(cards=cards)) == 2

    def test_without_inventory_or_cached_probe_is_zero(self):
        assert card_profile.detected_card_count() == 0

    def test_without_inventory_counts_the_cached_probe_and_never_probes(self, monkeypatch, mocker):
        probe = mocker.patch.object(cards_view, 'probe_now')
        _cache_probe(monkeypatch, 3)
        assert card_profile.detected_card_count() == 3
        probe.assert_not_called()


class TestAtStart:

    def test_cards_found_and_none_ticked_logs_the_hint(self, machine, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER)
        cap = job_processor.init_capacity()

        assert len(_hints(caplog)) == 1
        assert _hints(caplog)[0].levelno == logging.INFO
        assert cap.profile.cpu_only_hint is True
        assert (cap.profile.gpu_slot_count, cap.profile.cpu_slot_count, cap.profile.cards) == (0, 1, ())

    def test_no_card_found_logs_no_hint(self, machine, caplog):
        machine.present.clear()
        caplog.set_level(logging.INFO, logger=LOGGER)
        cap = job_processor.init_capacity()
        assert _hints(caplog) == [] and cap.profile.cpu_only_hint is False

    def test_ticked_card_logs_no_hint(self, machine, caplog):
        machine.compute['cards'] = [machine.record(0)]  # ticked for Unreal under method cpu
        caplog.set_level(logging.INFO, logger=LOGGER)
        job_processor.init_capacity()
        assert _hints(caplog) == []

    def test_method_gpu_without_ticks_logs_the_problem_not_the_hint(self, machine, caplog):
        machine.compute['method'] = 'gpu'
        caplog.set_level(logging.INFO, logger=LOGGER)
        job_processor.init_capacity()
        assert _hints(caplog) == [] and in_use.NO_TICK_TEXT in _messages(caplog)

    def test_unreadable_settings_log_no_hint(self, machine, caplog):
        machine.fail_read = True
        caplog.set_level(logging.INFO, logger=LOGGER)
        cap = job_processor.init_capacity()
        assert _hints(caplog) == [] and cap.profile.method == 'cpu'

    def test_card_list_already_logged_falls_back_to_the_cached_probe(self, machine, monkeypatch, caplog):
        monkeypatch.setattr(startup_log, '_done', True)
        caplog.set_level(logging.INFO, logger=LOGGER)
        job_processor.init_capacity()
        assert _hints(caplog) == []  # no inventory and nothing cached: no hint on a guess

        job_processor._capacity = None
        _cache_probe(monkeypatch, 1)
        job_processor.init_capacity()
        assert len(_hints(caplog)) == 1


class TestAfterRebuild:

    @pytest.fixture
    def ticked_start(self, monkeypatch, quiet_host):
        """Started with both cards ticked; the startup card list is already logged."""
        env = CardEnv(monkeypatch, count=2, method='cpu_gpu')
        return env, job_processor.init_capacity()

    def test_unticking_every_card_logs_the_hint_after_the_rebuild(self, ticked_start, monkeypatch, caplog):
        env, cap = ticked_start
        _cache_probe(monkeypatch, 2)
        env.compute.update(method='cpu', cards=[])
        rebuild.request_rebuild()
        caplog.set_level(logging.INFO, logger=LOGGER)

        assert rebuild.step(cap) == rebuild.SWAPPED

        messages = _messages(caplog)
        assert messages[0] == REBUILT and messages[-1] == HINT
        assert len(_hints(caplog)) == 1
        assert cap.profile.cpu_only_hint is True and cap.profile.gpu_slot_count == 0
        assert env.probes == 1  # the start only: the rebuild without ticks probes nothing (B6)

    def test_rebuild_without_a_cached_probe_logs_no_hint(self, ticked_start, caplog):
        env, cap = ticked_start
        env.compute.update(method='cpu', cards=[])
        rebuild.request_rebuild()
        caplog.set_level(logging.INFO, logger=LOGGER)

        assert rebuild.step(cap) == rebuild.SWAPPED
        assert REBUILT in _messages(caplog) and _hints(caplog) == []

    def test_rebuild_with_a_ticked_card_logs_no_hint(self, ticked_start, monkeypatch, caplog):
        env, cap = ticked_start
        _cache_probe(monkeypatch, 2)
        env.compute.update(method='cpu', cards=[env.record(0)])
        rebuild.request_rebuild()
        caplog.set_level(logging.INFO, logger=LOGGER)

        assert rebuild.step(cap) == rebuild.SWAPPED
        assert _hints(caplog) == []

    def test_unreadable_settings_stay_pending_with_no_hint(self, ticked_start, monkeypatch, caplog):
        env, cap = ticked_start
        _cache_probe(monkeypatch, 2)
        env.compute.update(method='cpu', cards=[])
        env.fail_read = True
        rebuild.request_rebuild()
        caplog.set_level(logging.INFO, logger=LOGGER)

        assert rebuild.step(cap) is None
        assert _hints(caplog) == [] and rebuild.pending() is True


class TestPeriodicCheck:

    def test_periodic_check_never_logs_the_hint(self, machine, monkeypatch, caplog):
        cap = job_processor.init_capacity()
        _cache_probe(monkeypatch, 2)
        caplog.set_level(logging.INFO, logger=LOGGER)
        caplog.clear()

        for _ in range(3):
            assert cap.assert_gpu_list_unchanged() is None

        assert _hints(caplog) == []
        assert rebuild.pending() is False  # the hint is not part of the comparison: no rebuild
        assert rebuild.step(cap) is None and _hints(caplog) == []
