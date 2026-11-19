# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
FR-CR-1 and FR-CR-6 wiring in ``system_monitor``.

- Registration and every heartbeat carry the ``cards`` object; an
  unreadable ``config.json`` leaves it out and the heartbeat is still sent.
- The ``dismissed_card_alerts`` of the authenticated answer (registration
  or heartbeat) are applied; a failed heartbeat applies nothing. During
  registration the capacity does not exist yet and the rebuild request is
  still recorded (FR-CL-4).

The probe is the autouse empty one (``_no_card_probe_in_heartbeats``); the
saved section lives in a dict (``_card_pages.fake_saved``).
"""
import pytest

from sethlans_worker_agent import config_compute, config_store, job_processor, system_monitor
from sethlans_worker_agent.capacity import rebuild
from tests.unit.worker._card_pages import fake_saved

OPEN = 'a' * 32
ALERT = {'id': OPEN, 'kind': 'card_lost', 'key': 'pci:0000:23:00.0', 'name': 'AMD Radeon RX 7900 XT',
         'detail': 'missing from the card list', 'raised_at': '2026-11-19T08:00:00+00:00'}


@pytest.fixture(autouse=True)
def _monitor(mocker):
    saved_id = system_monitor.WORKER_ID
    mocker.patch.object(system_monitor, 'get_system_info', return_value={'hostname': 'w1', 'os': 'linux'})
    mocker.patch.object(system_monitor, '_get_ui_url', return_value='https://127.0.0.1:8081')
    mocker.patch.object(system_monitor, '_get_ui_cert_fingerprint', return_value='ab' * 32)
    mocker.patch.object(system_monitor, '_process_heartbeat_versions', return_value=True)
    mocker.patch.object(system_monitor, '_add_unreal_payload')
    mocker.patch.object(system_monitor, '_add_active_job_ids')
    mocker.patch('sethlans_worker_agent.config.get_schedule_config', return_value={})
    mocker.patch('sethlans_worker_agent.config.API_TOKEN', 'test-token-abc')
    yield
    system_monitor.WORKER_ID = saved_id


def _sender(mocker, answer):
    sent = []

    def send(payload):
        sent.append(payload)
        return answer
    mocker.patch('sethlans_worker_agent.api_handler.send_authenticated_heartbeat', side_effect=send)
    return sent


class TestPayload:

    def test_registration_carries_cards(self, mocker, monkeypatch):
        fake_saved(monkeypatch, {'revision': 4})
        sent = _sender(mocker, {'id': 11})

        assert system_monitor.register_with_manager() == 11

        report = sent[0]['cards']
        assert (report['schema'], report['method'], report['revision'], report['cards']) == (1, 'cpu', 4, [])

    def test_heartbeat_carries_cards(self, mocker, monkeypatch):
        fake_saved(monkeypatch, {'revision': 5, 'alerts': [ALERT]})
        system_monitor.WORKER_ID = 7
        sent = _sender(mocker, {'id': 7})

        system_monitor.send_heartbeat()

        report = sent[0]['cards']
        assert report['revision'] == 5 and [alert['id'] for alert in report['alerts']] == [OPEN]

    def test_unreadable_config_leaves_cards_out_and_still_sends(self, mocker, monkeypatch):
        def unreadable():
            raise config_store.ConfigReadError('busy')
        monkeypatch.setattr(config_compute, '_read_raw', unreadable)
        system_monitor.WORKER_ID = 7
        sent = _sender(mocker, {'id': 7})

        system_monitor.send_heartbeat()

        assert len(sent) == 1 and 'cards' not in sent[0]


class TestDismissals:

    def test_registration_answer_is_applied_before_the_capacity_exists(self, mocker, monkeypatch):
        state, _writes = fake_saved(monkeypatch, {'revision': 0, 'alerts': [ALERT]})
        _sender(mocker, {'id': 11, 'dismissed_card_alerts': [OPEN]})
        assert job_processor.get_worker_capacity() is None

        system_monitor.register_with_manager()

        assert state['compute']['alerts'] == []
        assert rebuild.pending() is True

    def test_heartbeat_answer_is_applied(self, mocker, monkeypatch):
        state, _writes = fake_saved(monkeypatch, {'revision': 0, 'alerts': [ALERT]})
        system_monitor.WORKER_ID = 7
        _sender(mocker, {'id': 7, 'dismissed_card_alerts': [OPEN]})

        system_monitor.send_heartbeat()

        assert state['compute']['alerts'] == []
        assert rebuild.pending() is True

    def test_failed_heartbeat_applies_nothing(self, mocker, monkeypatch):
        apply = mocker.patch('sethlans_worker_agent.cards.choice.apply_manager_dismissals')
        system_monitor.WORKER_ID = 7
        _sender(mocker, None)

        system_monitor.send_heartbeat()

        apply.assert_not_called()

    def test_registration_answer_without_an_id_applies_nothing(self, mocker, monkeypatch):
        state, _writes = fake_saved(monkeypatch, {'revision': 0, 'alerts': [ALERT]})
        _sender(mocker, {'dismissed_card_alerts': [OPEN]})

        assert system_monitor.register_with_manager() is None

        assert len(state['compute']['alerts']) == 1
