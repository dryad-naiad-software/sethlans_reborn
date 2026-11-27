# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
#329: the registration log line when no required Blender version is installed yet.

Waiting for the manager to fetch the build is the normal first start: INFO.
A real install failure stays an ERROR. Never CRITICAL. Version sync and the
manager wait table are faked; no heartbeat is sent.
"""
import logging

import pytest

from sethlans_worker_agent import blender_wait, system_monitor


@pytest.fixture(autouse=True)
def _monitor(mocker):
    saved = system_monitor.WORKER_ID, system_monitor._versions_ready
    mocker.patch('sethlans_worker_agent.config.API_TOKEN', 'test-token-abc')
    mocker.patch.object(system_monitor, 'get_system_info', return_value={'hostname': 'w1'})
    mocker.patch.object(system_monitor, '_get_ui_url', return_value=None)
    mocker.patch.object(system_monitor, '_get_ui_cert_fingerprint', return_value='')
    mocker.patch.object(system_monitor, '_add_unreal_payload')
    mocker.patch.object(system_monitor.card_payload, 'add_to')
    mocker.patch.object(system_monitor.card_choice, 'apply_manager_dismissals')
    mocker.patch.object(system_monitor.api_handler, 'send_authenticated_heartbeat', return_value={'id': 11})
    mocker.patch.object(system_monitor.version_sync, 'sync_versions', return_value=False)
    yield
    system_monitor.WORKER_ID, system_monitor._versions_ready = saved


@pytest.mark.parametrize('waiting, level, text', [
    (['4.5.8'], logging.INFO, "Waiting for the manager to fetch Blender 4.5.8."),
    ([], logging.ERROR, "Failed to install any required Blender versions."),
], ids=['manager-fetching', 'install-failed'])
def test_not_ready_registration_logs_wait_as_info_and_failure_as_error(mocker, caplog, waiting, level, text):
    """#329: a manager still fetching the build logs INFO; a real install failure logs ERROR; none is CRITICAL.
    The worker stays registered with its versions not ready either way."""
    mocker.patch.object(blender_wait, 'waiting_versions', return_value=waiting)

    with caplog.at_level(logging.DEBUG, logger=system_monitor.__name__):
        assert system_monitor.register_with_manager() == 11

    lines = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == system_monitor.__name__]
    assert [lvl for lvl, msg in lines if msg.startswith(text)] == [level]
    assert all(lvl < logging.CRITICAL for lvl, _ in lines)
    assert system_monitor.are_versions_ready() is False
