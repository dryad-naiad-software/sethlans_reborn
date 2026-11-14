# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Registration and heartbeat payloads (Unreal foundation FR-WD-8, FR-RT-12,
FR-RT-16, FR-RT-17, NF-6, AC-9, AC-29, AC-33, AC-35; FR-T-7 heartbeat part,
FR-WX-12 registration/heartbeat half).

Real ``system_monitor`` + ``unreal_bridge`` + capabilities; only the HTTP
call, hardware info and the machine-specific Unreal pieces are faked.
"""
import re

import pytest

from sethlans_worker_agent import boot_id, job_processor, system_monitor, unreal_share
from tests._unreal_worker_state import (  # noqa: F401
    BUILD_58, FakeSampler, enable_unreal, reachable, reading, unreal_runtime_reset,
)
from tests.unit.worker.executors._fakes import fail_imports, reset_chunk3_state

pytestmark = pytest.mark.usefixtures('unreal_runtime_reset')

FAILING_IMPORTS = [
    'sethlans_worker_agent.unreal_executor',
    'sethlans_worker_agent.unreal_executor.capabilities',
    'sethlans_worker_agent.unreal_share',
]


@pytest.fixture(autouse=True)
def _monitor_state(mocker):
    reset_chunk3_state()
    saved = system_monitor.WORKER_ID
    mocker.patch('sethlans_worker_agent.system_monitor.get_system_info',
                 side_effect=lambda: {'hostname': 'w1', 'os': 'Windows', 'available_tools': {}})
    mocker.patch('sethlans_worker_agent.system_monitor._get_ui_url', return_value=None)
    mocker.patch('sethlans_worker_agent.system_monitor._get_ui_cert_fingerprint', return_value='')
    mocker.patch('sethlans_worker_agent.system_monitor._process_heartbeat_versions', return_value=True)
    mocker.patch('sethlans_worker_agent.config.get_schedule_config', return_value={})
    mocker.patch('sethlans_worker_agent.config.API_TOKEN', 'token')
    yield
    system_monitor.WORKER_ID = saved
    reset_chunk3_state()


def _capture(mocker, response=None):
    sent = []

    def send(payload):
        sent.append(dict(payload))
        return response or {'id': 7}

    mocker.patch('sethlans_worker_agent.api_handler.send_authenticated_heartbeat', side_effect=send)
    return sent


def _register_and_beat(mocker, response=None):
    sent = _capture(mocker, response)
    assert system_monitor.register_with_manager() == 7
    system_monitor.send_heartbeat()
    return sent


class TestDisabled:

    def test_payloads(self, mocker):
        with job_processor._active_jobs_lock:
            job_processor._unfinished_job_ids.update({12, 3})
        registration, heartbeat = _register_and_beat(mocker)
        disabled = {'schema': 1, 'enabled': False, 'disabled_reason': 'disabled by config',
                    'installs': [], 'share': '', 'share_reachable': False}
        assert registration['unreal_capabilities'] == disabled
        assert heartbeat['unreal_capabilities'] == disabled
        assert 'gpu_memory' not in registration and 'gpu_memory' not in heartbeat
        assert re.fullmatch(r'[0-9a-f]{32}', registration['worker_boot_id'])
        assert registration['worker_boot_id'] == boot_id.BOOT_ID
        assert 'worker_boot_id' not in heartbeat
        assert heartbeat['active_job_ids'] == [3, 12]
        assert 'active_job_ids' not in registration
        assert heartbeat['available_tools'] == {}

    def test_active_job_ids_empty_list(self, mocker):
        _registration, heartbeat = _register_and_beat(mocker)
        assert heartbeat['active_job_ids'] == []

    def test_active_job_ids_omitted_on_failure(self, mocker):
        mocker.patch.object(job_processor, 'get_unfinished_job_ids', side_effect=RuntimeError('x'))
        _registration, heartbeat = _register_and_beat(mocker)
        assert 'active_job_ids' not in heartbeat


class TestEnabled:

    def test_share_reachability_and_gpu_memory(self, mocker, monkeypatch):
        enable_unreal(monkeypatch, share=r'\\nas.lan\sethlans', share_state=reachable(),
                      sampler=FakeSampler(reading(taken_at=0.0)))
        registration, heartbeat = _register_and_beat(mocker)
        for payload in (registration, heartbeat):
            caps = payload['unreal_capabilities']
            assert caps['enabled'] is True and caps['share'] == r'\\nas.lan\sethlans'
            assert caps['share_reachable'] is True
            assert caps['installs'] == [{'build': BUILD_58, 'supported': True, 'source': 'launcher'}]
            gpus = payload['gpu_memory']['gpus']
            assert gpus[0]['total_mb'] == 32607 and gpus[0]['sethlans_used_mb'] is None

    def test_no_reading_omits_gpu_memory(self, mocker, monkeypatch):
        enable_unreal(monkeypatch, sampler=FakeSampler(None))
        registration, heartbeat = _register_and_beat(mocker)
        assert 'gpu_memory' not in registration and 'gpu_memory' not in heartbeat

    def test_response_share_key_is_ignored(self, mocker, monkeypatch):
        """A v3 manager's heartbeat ``sethlans_share`` never changes the worker's share."""
        enable_unreal(monkeypatch, share=r'\\nas.lan\sethlans')
        sent = _register_and_beat(mocker, response={'id': 7, 'sethlans_share': r'\\evil\x'})
        system_monitor.send_heartbeat()
        assert unreal_share.state().root.canonical == r'\\nas.lan\sethlans'
        assert all(p['unreal_capabilities']['share'] == r'\\nas.lan\sethlans' for p in sent)

    def test_unreachable_share_is_reported(self, mocker, monkeypatch):
        enable_unreal(monkeypatch, share=r'\\nas.lan\sethlans', share_state=reachable(ok=False))
        _registration, heartbeat = _register_and_beat(mocker)
        assert heartbeat['unreal_capabilities']['share_reachable'] is False

    def test_force_gpu_index_reported_disabled(self, mocker, monkeypatch):
        enable_unreal(monkeypatch, force_gpu_index='0')
        _registration, heartbeat = _register_and_beat(mocker)
        caps = heartbeat['unreal_capabilities']
        assert caps['enabled'] is False and caps['disabled_reason'].startswith('SETHLANS_FORCE_GPU_INDEX is set')


@pytest.mark.parametrize('target', FAILING_IMPORTS)
def test_import_failure_keeps_registration_and_heartbeat_working(mocker, monkeypatch, target):
    """AC-29 / FR-WX-12: the bridge's fallback ``unreal_capabilities`` is sent.

    ``capabilities`` is forgotten first, as in a fresh process where it never
    loaded, so a failing ``unreal_share`` import is actually reached.
    """
    import sys
    from sethlans_worker_agent import unreal_executor
    monkeypatch.delitem(sys.modules, 'sethlans_worker_agent.unreal_executor.capabilities')
    monkeypatch.delattr(unreal_executor, 'capabilities')
    fail_imports(monkeypatch, target)
    registration, heartbeat = _register_and_beat(mocker)
    for payload in (registration, heartbeat):
        caps = payload['unreal_capabilities']
        assert caps['enabled'] is False
        assert caps['disabled_reason'] == 'Unreal support failed to load: ImportError'
        assert 'gpu_memory' not in payload
    assert registration['worker_boot_id'] == boot_id.BOOT_ID
    assert heartbeat['active_job_ids'] == []
