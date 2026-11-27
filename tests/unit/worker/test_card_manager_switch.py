# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The manager-changes switch and the last manager change on the worker (FR-CM-1, FR-CM-7, FR-CW-2..4, FR-CR-1).

- ``POST /api/control/cards/manager-changes`` ``{"allow": bool}``: 401 without the password, 400 for any other
  body, 409 under ``SETHLANS_COMPUTE_ALLOW_MANAGER_CHANGES``, 503 when it cannot be written, else 200 with one
  INFO line; the revision does not change.
- The card GET reports ``allow_manager_changes``, ``allow_manager_changes_env``, ``manager_changes_allowed``
  and ``last_request``; the heartbeat report carries ``request_outcome`` without the requester.
- ``send_heartbeat`` hands the answer to ``cards.remote``; registration does not (FR-CM-3).
"""
import logging

import pytest

from sethlans_worker_agent import config_compute, config_store, system_monitor
from sethlans_worker_agent.cards import payload, remote
from sethlans_worker_agent.web_ui.runtime import handlers_cards
from tests.unit.worker._card_pages import RUNTIME_GET, call, fake_probes, fake_saved

SWITCH = '/api/control/cards/manager-changes'
RECORD = {'id': 'ef' * 16, 'result': 'refused', 'code': 'not_allowed', 'reason': 'switched off', 'revision': 4,
          'requested_by': 'user id=12', 'requested_at': '2026-11-19T10:00:00+00:00',
          'finished_at': '2026-11-19T10:00:05+00:00'}


@pytest.fixture
def auth(fresh_shutdown_event, bearer_header):
    return bearer_header


class TestSwitchEndpoint:

    @pytest.mark.parametrize('allow', [False, True])
    def test_saves_without_changing_the_revision_and_logs_once(self, monkeypatch, auth, caplog, allow):
        state, writes = fake_saved(monkeypatch, {'revision': 4, 'allow_manager_changes': not allow})
        with caplog.at_level(logging.INFO, logger='sethlans_worker_agent.cards.store_requests'):
            status, answer = call(SWITCH, 'POST', {'allow': allow}, auth)

        assert status == 200 and answer['message'] == handlers_cards.SWITCH_SAVED_TEXT
        assert (answer['allow_manager_changes'], answer['manager_changes_allowed']) == (allow, allow)
        assert writes == [[('compute.allow_manager_changes', allow)]] and state['compute']['revision'] == 4
        lines = [r.getMessage() for r in caplog.records if r.name.endswith('store_requests')]
        assert lines == [f"Manager changes to the graphics card choice allowed: {'yes' if allow else 'no'} (set on "
                         f"the settings page)."]

    def test_needs_the_password(self, monkeypatch, auth):
        _state, writes = fake_saved(monkeypatch)
        assert call(SWITCH, 'POST', {'allow': False})[0] == 401
        assert writes == []

    @pytest.mark.parametrize('value', [{'allow': 'false'}, {'allow': 0}, {}, {'allow': True, 'x': 1},
                                       {'enabled': True}, [True], None])
    def test_other_bodies_are_400(self, monkeypatch, auth, value):
        _state, writes = fake_saved(monkeypatch)
        status, answer = call(SWITCH, 'POST', value, auth)
        assert status == 400 and answer['error'] == ("The request must be a JSON object with allow set to true or "
                                                     "false.")
        assert writes == []

    def test_409_under_its_environment_variable(self, monkeypatch, auth):
        _state, writes = fake_saved(monkeypatch)
        monkeypatch.setenv(config_compute.ENV_ALLOW, 'true')
        assert call(SWITCH, 'POST', {'allow': False}, auth) == (409, {'error': handlers_cards.SWITCH_ENV_TEXT})
        assert writes == []

    def test_choice_env_override_does_not_block_the_switch(self, monkeypatch, auth):
        _state, writes = fake_saved(monkeypatch)
        monkeypatch.setenv(config_compute.ENV_METHOD, 'cpu')
        status, answer = call(SWITCH, 'POST', {'allow': True}, auth)
        assert status == 200 and answer['manager_changes_allowed'] is False and len(writes) == 1

    def test_failed_write_is_503(self, monkeypatch, auth):
        fake_saved(monkeypatch)

        def fail(_pairs):
            raise OSError('disk full')

        monkeypatch.setattr(config_store, 'set_many', fail)
        assert call(SWITCH, 'POST', {'allow': False}, auth) == (503, {'error': handlers_cards.SWITCH_NOT_SAVED_TEXT})


class TestGetFields:

    def test_runtime_get_reports_the_switch_and_the_last_change(self, monkeypatch):
        fake_saved(monkeypatch, {'revision': 4, 'allow_manager_changes': False, 'last_request': dict(RECORD)})
        fake_probes(monkeypatch)
        answer = call(RUNTIME_GET)[1]
        assert (answer['allow_manager_changes'], answer['allow_manager_changes_env'],
                answer['manager_changes_allowed']) == (False, False, False)
        assert answer['last_request'] == RECORD

    def test_defaults_and_env(self, monkeypatch):
        fake_saved(monkeypatch)
        fake_probes(monkeypatch)
        monkeypatch.setenv(config_compute.ENV_ALLOW, 'true')
        answer = call(RUNTIME_GET)[1]
        assert (answer['allow_manager_changes'], answer['allow_manager_changes_env'],
                answer['manager_changes_allowed'], answer['last_request']) == (True, True, True, None)

    @pytest.mark.parametrize('change', [{'requested_by': 'alice'}, {'requested_by': None}])
    def test_a_requester_that_is_not_a_user_id_is_not_shown(self, monkeypatch, change):
        fake_saved(monkeypatch, {'revision': 4, 'last_request': dict(RECORD, **change)})
        fake_probes(monkeypatch)
        assert call(RUNTIME_GET)[1]['last_request']['requested_by'] is None

    def test_heartbeat_reports_the_outcome_without_the_requester(self, monkeypatch):
        fake_saved(monkeypatch, {'revision': 4, 'last_request': dict(RECORD)})
        report = payload.build()
        assert report['request_outcome'] == {name: RECORD[name] for name in
                                             ('id', 'result', 'code', 'reason', 'revision', 'finished_at')}

    @pytest.mark.parametrize('record', [{'id': 'x'}, dict(RECORD, code='applied'), dict(RECORD, result='done'), 'x'])
    def test_a_broken_saved_outcome_is_not_reported(self, monkeypatch, record):
        fake_saved(monkeypatch, {'revision': 4, 'last_request': record})
        assert payload.build()['request_outcome'] is None


class TestHeartbeatWiring:

    @pytest.fixture
    def handled(self, monkeypatch):
        seen = []
        monkeypatch.setattr(remote, 'handle_response', seen.append)
        monkeypatch.setattr(system_monitor, 'WORKER_ID', None)  # restored after the test
        monkeypatch.setattr(system_monitor, 'get_system_info', lambda: {'hostname': 'w1', 'os': 'linux'})
        monkeypatch.setattr(system_monitor, '_get_ui_url', lambda: None)
        monkeypatch.setattr(system_monitor, '_get_ui_cert_fingerprint', lambda: '')
        monkeypatch.setattr(system_monitor, '_add_unreal_payload', lambda _p: None)
        monkeypatch.setattr(system_monitor, '_add_active_job_ids', lambda _p: None)
        monkeypatch.setattr(system_monitor, '_process_heartbeat_versions', lambda *_a, **_k: True)
        monkeypatch.setattr(system_monitor.config, 'get_schedule_config', lambda: {})
        monkeypatch.setattr(system_monitor.config, 'API_TOKEN', 'token')
        return seen

    def test_heartbeat_answer_is_handed_over(self, monkeypatch, handled):
        reply = {'id': 3, 'card_choice_request': None}
        monkeypatch.setattr(system_monitor.api_handler, 'send_authenticated_heartbeat', lambda _p: reply)
        monkeypatch.setattr(system_monitor, 'WORKER_ID', 3)
        system_monitor.send_heartbeat()
        assert handled == [reply]

    def test_registration_answer_is_not(self, monkeypatch, handled):
        reply = {'id': 3, 'card_choice_request': {'id': 'ab' * 16}}
        monkeypatch.setattr(system_monitor.api_handler, 'send_authenticated_heartbeat', lambda _p: reply)
        assert system_monitor.register_with_manager() == 3
        assert handled == []

    def test_a_real_request_in_a_heartbeat_is_applied(self, monkeypatch):
        """End to end through ``send_heartbeat`` with the real handler (AC-23, worker part)."""
        from tests.unit.worker.cards._remote_helpers import Machine, answer, request
        machine = Machine(monkeypatch)
        for name, value in (('WORKER_ID', 3), ('get_system_info', lambda: {}), ('_get_ui_url', lambda: None),
                            ('_get_ui_cert_fingerprint', lambda: ''), ('_add_unreal_payload', lambda _p: None),
                            ('_add_active_job_ids', lambda _p: None),
                            ('_process_heartbeat_versions', lambda *_a, **_k: True)):
            monkeypatch.setattr(system_monitor, name, value)
        monkeypatch.setattr(system_monitor.config, 'get_schedule_config', lambda: {})
        monkeypatch.setattr(system_monitor.api_handler, 'send_authenticated_heartbeat',
                            lambda _p: answer(request()))
        system_monitor.send_heartbeat()
        assert machine.compute['revision'] == 1 and machine.last_request['code'] == 'applied'
        assert machine.rebuilds == 1
