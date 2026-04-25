# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the system_monitor manager-setup-complete capture (issue #126).

Verifies that ``send_heartbeat`` and ``register_with_manager`` extract the
``manager_setup_complete`` field from the heartbeat response and update the
module-level cache, with default-on-missing-field set to True (backward
compatibility for older managers).
"""
import pytest

from sethlans_worker_agent import system_monitor


@pytest.fixture(autouse=True)
def _reset_system_monitor_state():
    """Reset module-level state between tests."""
    system_monitor._manager_setup_complete = True
    system_monitor._versions_ready = False
    saved_worker_id = system_monitor.WORKER_ID
    yield
    system_monitor._manager_setup_complete = True
    system_monitor._versions_ready = False
    system_monitor.WORKER_ID = saved_worker_id


def _patch_common(mocker):
    """Mocks shared by all heartbeat/register tests."""
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_system_info',
        return_value={'hostname': 'w1', 'os': 'linux'},
    )
    mocker.patch(
        'sethlans_worker_agent.system_monitor._get_ui_url',
        return_value='https://127.0.0.1:8081',
    )
    mocker.patch(
        'sethlans_worker_agent.system_monitor._get_ui_cert_fingerprint',
        return_value='ab' * 32,
    )
    mocker.patch(
        'sethlans_worker_agent.system_monitor._process_heartbeat_versions',
        return_value=True,
    )
    mocker.patch(
        'sethlans_worker_agent.config.get_schedule_config',
        return_value={},
    )
    mocker.patch(
        'sethlans_worker_agent.config.API_TOKEN', 'test-token-abc',
    )


class TestIsManagerSetupCompleteDefault:

    def test_default_state_is_true(self):
        # Reset is performed by autouse fixture
        assert system_monitor.is_manager_setup_complete() is True


class TestSendHeartbeatCapturesField:

    def test_response_with_field_true_keeps_state_true(self, mocker):
        _patch_common(mocker)
        system_monitor.WORKER_ID = 7
        mocker.patch(
            'sethlans_worker_agent.api_handler.send_authenticated_heartbeat',
            return_value={'id': 7, 'manager_setup_complete': True},
        )

        system_monitor.send_heartbeat(is_busy=False, active_jobs={})

        assert system_monitor.is_manager_setup_complete() is True

    def test_response_with_field_false_flips_state_false(self, mocker):
        _patch_common(mocker)
        system_monitor.WORKER_ID = 7
        mocker.patch(
            'sethlans_worker_agent.api_handler.send_authenticated_heartbeat',
            return_value={'id': 7, 'manager_setup_complete': False},
        )

        system_monitor.send_heartbeat(is_busy=False, active_jobs={})

        assert system_monitor.is_manager_setup_complete() is False

    def test_response_missing_field_defaults_to_true(self, mocker):
        """If the manager omits the field (older build), state defaults True
        even if the previous value was False (backward-compat contract)."""
        _patch_common(mocker)
        system_monitor.WORKER_ID = 7
        # Pre-set to False to confirm default-on-missing actually runs.
        system_monitor._manager_setup_complete = False
        mocker.patch(
            'sethlans_worker_agent.api_handler.send_authenticated_heartbeat',
            return_value={'id': 7},
        )

        system_monitor.send_heartbeat(is_busy=False, active_jobs={})

        assert system_monitor.is_manager_setup_complete() is True


class TestRegisterWithManagerCapturesField:

    def test_register_response_updates_state(self, mocker):
        _patch_common(mocker)
        mocker.patch(
            'sethlans_worker_agent.api_handler.send_authenticated_heartbeat',
            return_value={'id': 11, 'manager_setup_complete': False},
        )

        worker_id = system_monitor.register_with_manager()

        assert worker_id == 11
        assert system_monitor.is_manager_setup_complete() is False
