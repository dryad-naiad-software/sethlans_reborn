# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``unreal_bridge`` fallbacks and import isolation (Unreal foundation
FR-WX-1, FR-WX-12, NF-6; FR-T-5 "Bridge" and "Import isolation"; AC-29).

The registration/heartbeat half of FR-WX-12 needs the ``system_monitor``
payload wiring (FR-WD-8, spec chunk 4) and is tested there.
"""
import pytest

from sethlans_worker_agent import job_processor, unreal_bridge
from sethlans_worker_agent.job_lifecycle import process_claimed_job

from ..executors._fakes import blender_job, fail_imports, install_capacity

FAILING_IMPORTS = [
    'sethlans_worker_agent.unreal_executor',
    'sethlans_worker_agent.unreal_executor.capabilities',
    'sethlans_worker_agent.unreal_share',
]


class TestDefaults:

    def test_disabled_values(self):
        assert unreal_bridge.poll_params(node_idle=True) == {}
        assert unreal_bridge.poll_params(node_idle=False) == {}
        assert unreal_bridge.gpu_memory_payload() is None
        snap = unreal_bridge.snapshot()
        assert snap == {
            'schema': 1, 'enabled': False, 'disabled_reason': 'disabled by config',
            'installs': [], 'share': '', 'share_reachable': False,
        }

    def test_drift_allowed_without_capacity_or_exclusive(self):
        assert unreal_bridge.drift_check_allowed() is True
        cap = install_capacity()
        cap.reserve_for_job(1, 'GPU')
        assert unreal_bridge.drift_check_allowed() is True
        cap.release_slot(1)
        cap.reserve_exclusive(2)
        assert unreal_bridge.drift_check_allowed() is False


class TestFallbacks:

    def test_each_function_falls_back_and_logs_once(self, monkeypatch, caplog):
        caplog.set_level('ERROR', logger='sethlans_worker_agent.unreal_bridge')
        fail_imports(monkeypatch, 'sethlans_worker_agent.unreal_executor')
        for _ in range(3):
            assert unreal_bridge.poll_params(node_idle=True) == {}
            assert unreal_bridge.gpu_memory_payload() is None
            snap = unreal_bridge.snapshot()
        assert snap['enabled'] is False
        assert snap['disabled_reason'] == 'Unreal support failed to load: ImportError'
        assert snap['installs'] == [] and snap['share'] == ''
        errors = [r for r in caplog.records if r.levelname == 'ERROR']
        assert len(errors) == 3  # one per function, not per call

    def test_drift_check_falls_back_to_allowed(self, monkeypatch, caplog):
        caplog.set_level('ERROR', logger='sethlans_worker_agent.unreal_bridge')

        def broken():
            raise RuntimeError('boom')

        monkeypatch.setattr(job_processor, 'get_worker_capacity', broken)
        assert unreal_bridge.drift_check_allowed() is True
        assert unreal_bridge.drift_check_allowed() is True
        assert len([r for r in caplog.records if r.levelname == 'ERROR']) == 1

    def test_underlying_exception_is_contained(self, monkeypatch):
        from sethlans_worker_agent.unreal_executor import capabilities

        def broken(node_idle):
            raise OSError('share')

        monkeypatch.setattr(capabilities, 'poll_params', broken)
        assert unreal_bridge.poll_params(node_idle=True) == {}


@pytest.mark.parametrize('target', FAILING_IMPORTS)
class TestImportIsolation:

    def _baseline_params(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.job_processor.tool_manager_instance.scan_for_local_blenders',
            return_value=[{'version': '4.2.1'}],
        )
        install_capacity(gpu_slot_count=1, cpu_slot_count=1)
        return job_processor._build_poll_params(['GPU', 'CPU', 'ANY'])

    def test_poll_params_unchanged(self, mocker, monkeypatch, target):
        expected = {
            'status': 'QUEUED', 'assigned_worker__isnull': 'true',
            'device_prefs': 'GPU,CPU,ANY', 'available_versions': '4.2.1',
        }
        assert self._baseline_params(mocker) == expected
        fail_imports(monkeypatch, target)
        assert job_processor._build_poll_params(['GPU', 'CPU', 'ANY']) == expected

    def test_blender_job_runs_to_done(self, mocker, monkeypatch, target):
        fail_imports(monkeypatch, target)
        install_capacity()
        mocker.patch('sethlans_worker_agent.job_processor._build_poll_params', return_value={})
        mocker.patch('sethlans_worker_agent.api_handler.poll_for_available_jobs',
                     return_value=[blender_job(1)])
        mocker.patch('sethlans_worker_agent.api_handler.claim_job', return_value=True)
        mocker.patch(
            'sethlans_worker_agent.blender_executor.execute_blender_job',
            return_value=(True, False, False, '', '', '', None, None, None, None),
        )
        update = mocker.patch('sethlans_worker_agent.api_handler.update_job_status')
        process_claimed_job(job_processor.poll_and_claim_job(worker_id=1))
        assert update.call_args.args[1]['status'] == 'DONE'
