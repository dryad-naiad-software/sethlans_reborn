# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The card check at worker start and the level-triggered alerts (FR-CK-1, FR-CK-3, FR-CK-7, C4, C-20, AC-42).

- A ticked card missing at start is alerted at start (C4) and is not in use.
- A card whose alert was dismissed while it is still missing is alerted
  again at the next check.
- An unreadable ``config.json`` at start keeps the defaults (CPU only),
  says so, and a later check that can read it requests the rebuild that
  brings the saved choice in.
- ``init_capacity`` records the generation it built from (FR-CL-4).
"""
import logging

import pytest

from sethlans_worker_agent import job_processor, system_monitor
from sethlans_worker_agent.capacity import card_check, rebuild
from sethlans_worker_agent.cards import choice
from sethlans_worker_agent.cards import warnings as card_warnings
from tests.unit.worker._card_check_env import CardEnv
from tests.unit.worker.executors._fakes import fake_cards, key

CHECK = 'sethlans_worker_agent.capacity.card_check'


@pytest.fixture
def machine(monkeypatch, mocker):
    mocker.patch.object(system_monitor, 'get_gpu_device_details', return_value=[])
    mocker.patch.object(system_monitor, 'get_cpu_thread_count', return_value=8)
    return CardEnv(monkeypatch, count=2)


def _critical(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]


class TestStartCheck:

    def test_all_cards_present(self, machine):
        cap = job_processor.init_capacity()

        assert cap.profile.cards == fake_cards(2) and cap.profile.gpu_slot_count == 2
        assert machine.alerts() == [] and machine.detections == 0  # the start uses the cached list
        assert rebuild.pending() is False

    def test_card_missing_at_start_is_alerted_and_not_used(self, machine, caplog):
        machine.lose(1)

        with caplog.at_level(logging.CRITICAL, logger=CHECK):
            cap = job_processor.init_capacity()

        assert machine.alert_keys() == [('card_lost', key(1))]
        assert len(_critical(caplog)) == 1
        assert cap.profile.cards == fake_cards(1)

    def test_start_remembers_the_engine_warning_condition(self, machine):
        card_warnings.reset_for_tests(None)
        machine.compute['cards'] = [machine.record(0)]  # card 1 is not ticked: Eevee may land on it (D22)

        job_processor.init_capacity()

        assert card_warnings._condition is True

    def test_method_cpu_without_ticks_probes_nothing(self, machine):
        """B6: nothing ticked, nothing to check."""
        machine.compute.update(method='cpu', cards=[])

        cap = job_processor.init_capacity()

        assert machine.probes == 0 and cap.profile.gpu_slot_count == 0 and machine.alerts() == []

    def test_method_cpu_still_checks_the_ticked_cards(self, machine, caplog):
        """Cards ticked for Unreal under method cpu are checked too: a missing one is alerted."""
        machine.compute.update(method='cpu', cards=[machine.record(1)])
        machine.lose(1)

        with caplog.at_level(logging.CRITICAL, logger=CHECK):
            cap = job_processor.init_capacity()

        assert machine.probes == 1 and machine.detections == 0
        assert machine.alert_keys() == [('card_lost', key(1))]
        assert len(_critical(caplog)) == 1
        assert (cap.profile.method, cap.profile.gpu_slot_count, cap.profile.cards) == ('cpu', 0, ())
        assert rebuild.pending() is False

    def test_unreadable_settings_start_on_the_cpu_and_recover(self, machine, caplog):
        machine.fail_read = True
        with caplog.at_level(logging.ERROR, logger=CHECK):
            cap = job_processor.init_capacity()

        assert (cap.profile.method, cap.profile.gpu_slot_count) == ('cpu', 0)
        assert machine.probes == 0
        assert any('could not be read at start' in r.getMessage() for r in caplog.records)

        machine.fail_read = False
        assert cap.assert_gpu_list_unchanged() is None
        assert rebuild.pending() is True
        assert rebuild.step(cap) == rebuild.SWAPPED
        assert cap.profile.cards == fake_cards(2)

    def test_request_during_the_start_stays_pending(self, machine, mocker):
        real = card_check.start_check

        def start(section):
            rebuild.request_rebuild()  # e.g. a save on the setup page while the worker starts
            return real(section)
        mocker.patch.object(card_check, 'start_check', side_effect=start)

        job_processor.init_capacity()

        assert rebuild.pending() is True


class TestLevelTriggered:

    def test_dismissed_while_still_missing_is_alerted_again(self, machine, caplog):
        """AC-42 / C4."""
        machine.lose(1)
        cap = job_processor.init_capacity()
        [alert] = machine.alerts()

        assert [a['id'] for a in choice.dismiss_alerts([alert['id']], 'settings page')] == [alert['id']]
        assert machine.alerts() == [] and rebuild.pending() is True
        caplog.clear()

        with caplog.at_level(logging.CRITICAL, logger=CHECK):
            assert cap.assert_gpu_list_unchanged() is None

        assert machine.alert_keys() == [('card_lost', key(1))]
        assert machine.alerts()[0]['id'] != alert['id']
        assert len(_critical(caplog)) == 1

    def test_card_back_after_dismissal_returns_to_use(self, machine):
        """O8: back in the list, still held until dismissed; then the rebuild uses it again."""
        machine.lose(1)
        cap = job_processor.init_capacity()
        machine.present.add(key(1))

        assert cap.assert_gpu_list_unchanged() is None
        assert cap.profile.cards == fake_cards(1) and rebuild.pending() is False  # held: no change

        choice.dismiss_alerts([machine.alerts()[0]['id']], 'manager')
        assert rebuild.step(cap) == rebuild.SWAPPED
        assert cap.profile.cards == fake_cards(2) and machine.alerts() == []
