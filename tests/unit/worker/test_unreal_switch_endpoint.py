# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Unreal switch on the settings page (card selection O14, C-27 a).

``POST /api/control/cards/unreal`` ``{"enabled": true|false}`` takes the
Bearer password, shutdown guard and body cap of the other card actions,
saves ``unreal.enabled`` in the JSON config store for the next start (one
INFO line), answers 409 while ``SETHLANS_UNREAL_ENABLED`` sets it and 400
for anything but a JSON boolean. Both card GETs report ``unreal_enabled``,
``unreal_enabled_env`` and ``unreal_restart_needed``.
"""
import logging

import pytest

from sethlans_worker_agent import config_store, config_unreal, unreal_switch
from sethlans_worker_agent.web_ui.runtime import handlers_cards
from tests.unit.worker._card_pages import RUNTIME_GET, SETUP_PATH, call, fake_probes

SWITCH = '/api/control/cards/unreal'


@pytest.fixture
def saved_config(monkeypatch):
    """The JSON config store in a dict; no SETHLANS_UNREAL_ENABLED; Unreal off in this process."""
    state = {}
    writes = []

    def set_one(dotted, value):
        writes.append((dotted, value))
        state[dotted] = value

    monkeypatch.delenv(unreal_switch.ENV_VAR, raising=False)
    monkeypatch.setattr(config_store, 'set', set_one)
    monkeypatch.setattr(config_store, 'get', lambda dotted, default=None: state.get(dotted, default))
    monkeypatch.setattr(config_unreal, 'ENABLED', False)
    return state, writes


@pytest.fixture
def auth(setup_complete, fresh_shutdown_event, bearer_header):
    return bearer_header


class TestSave:

    def test_switch_on_saves_for_the_next_start(self, saved_config, auth, caplog):
        state, writes = saved_config
        with caplog.at_level(logging.INFO, logger='sethlans_worker_agent.unreal_switch'):
            status, answer = call(SWITCH, 'POST', {'enabled': True}, auth)
        assert status == 200 and answer['message'] == handlers_cards.UNREAL_SAVED_TEXT
        assert writes == [('unreal.enabled', True)]
        assert (answer['unreal_enabled'], answer['unreal_enabled_env'], answer['unreal_restart_needed']) == \
            (True, False, True)
        lines = [r.getMessage() for r in caplog.records if r.name == 'sethlans_worker_agent.unreal_switch']
        assert lines == ["Unreal switched on from the settings page; this takes effect at the next worker start."]

    def test_switching_back_needs_no_restart(self, saved_config, auth):
        call(SWITCH, 'POST', {'enabled': True}, auth)
        status, answer = call(SWITCH, 'POST', {'enabled': False}, auth)
        assert status == 200 and answer['unreal_enabled'] is False and answer['unreal_restart_needed'] is False

    def test_environment_variable_is_409(self, saved_config, auth, monkeypatch):
        _state, writes = saved_config
        monkeypatch.setenv(unreal_switch.ENV_VAR, 'true')
        status, answer = call(SWITCH, 'POST', {'enabled': False}, auth)
        assert (status, answer) == (409, {'error': 'This switch is set by an environment variable on this worker.'})
        assert writes == []

    @pytest.mark.parametrize('value', [{'enabled': 1}, {'enabled': 'true'}, {'enabled': None}, {}, [True],
                                       {'enabled': True, 'extra': 1}, 'on'])
    def test_anything_but_a_boolean_is_400(self, saved_config, auth, value):
        _state, writes = saved_config
        status, answer = call(SWITCH, 'POST', value, auth)
        assert status == 400 and answer['error'].endswith('.') and writes == []

    def test_needs_the_password(self, saved_config, setup_complete, fresh_shutdown_event):
        assert call(SWITCH, 'POST', {'enabled': True})[0] == 401 and saved_config[1] == []

    def test_refused_during_shutdown(self, saved_config, auth, fresh_shutdown_event):
        fresh_shutdown_event.set()
        assert call(SWITCH, 'POST', {'enabled': True}, auth)[0] == 503

    def test_failed_write_is_503(self, saved_config, auth, monkeypatch):
        monkeypatch.setattr(config_store, 'set', lambda dotted, value: (_ for _ in ()).throw(OSError('disk full')))
        assert call(SWITCH, 'POST', {'enabled': True}, auth) == (503, {'error': handlers_cards.UNREAL_NOT_SAVED_TEXT})


class TestGetFields:

    @pytest.mark.parametrize('path', [RUNTIME_GET, SETUP_PATH])
    def test_both_card_gets_report_the_switch(self, saved_config, monkeypatch, path, request):
        if path == RUNTIME_GET:
            request.getfixturevalue('setup_complete')   # the setup GET answers until setup is complete
        fake_probes(monkeypatch)
        from sethlans_worker_agent.web_ui import cards_cache
        cards_cache.reset_for_tests()
        answer = call(path)[1]
        assert (answer['unreal_enabled'], answer['unreal_enabled_env'], answer['unreal_restart_needed']) == \
            (False, False, False)

    def test_environment_value_and_running_value(self, saved_config, monkeypatch):
        monkeypatch.setenv(unreal_switch.ENV_VAR, 'yes')
        monkeypatch.setattr(config_unreal, 'ENABLED', True)
        assert unreal_switch.state() == {'unreal_enabled': True, 'unreal_enabled_env': True,
                                         'unreal_restart_needed': False}
