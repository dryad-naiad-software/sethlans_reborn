# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the GPU drift (hardware change) detector.

Covers FR-28 and #239: the list a fresh detection finds is compared with
the list the worker uses, by (device id, backend) per card and in order.
The same list is a no-op; any difference (count, id, backend, order) fires
the drift handler, terminates active jobs, sets the module-level exit-code
flag, and sets agent._shutdown_event. sys.exit is NEVER called from inside
the capacity module (FR-22a).
"""
import json
import logging
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

# agent.py runs argparse at import time; stub argv to avoid consuming
# pytest's command line.
_saved_argv = sys.argv
sys.argv = ['agent-test-runner']
try:
    from sethlans_worker_agent import agent as agent_module  # noqa: E402
finally:
    sys.argv = _saved_argv

from sethlans_worker_agent import hardware_detection  # noqa: E402
from sethlans_worker_agent.capacity import (  # noqa: E402
    CapacityProfile,
    WorkerCapacity,
    drift as drift_module,
)

RTX = {'index': 0, 'name': 'RTX', 'type': 'OPTIX', 'id': 'CUDA_RTX_0000:01:00_OptiX'}
AMD = {'index': 1, 'name': 'AMD', 'type': 'HIP', 'id': 'HIP_AMD_0000:03:00'}
IN_USE = [RTX, AMD]
RTX_PAIR, AMD_PAIR = (RTX['id'], 'OPTIX'), (AMD['id'], 'HIP')
SLOTS = 'sethlans_worker_agent.capacity.slots'


def _profile(startup_gpu_count=2):
    return CapacityProfile(
        gpu_slot_count=startup_gpu_count,
        cpu_slot_count=1,
        cpu_thread_ceiling=15,
        cpu_threads_effective=15,
        startup_gpu_count=startup_gpu_count,
        gpu_mode='split',
    )


@pytest.fixture
def env(mocker):
    """Worker using IN_USE; returns spies and a setter for the detection."""
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_gpu_device_details',
        return_value=IN_USE,
    )
    detect = mocker.patch(f'{SLOTS}.detect_physical_gpus_now', return_value=[RTX_PAIR, AMD_PAIR])
    terminate = mocker.patch('sethlans_worker_agent.job_processor.terminate_all_active_jobs_for_drift')
    event = threading.Event()
    mocker.patch.object(agent_module, '_shutdown_event', event)
    return SimpleNamespace(detect=detect, terminate=terminate, event=event, cap=WorkerCapacity(_profile()))


def _assert_no_action(env):
    env.terminate.assert_not_called()
    assert drift_module.get_drift_exit_code() is None
    assert env.event.is_set() is False


def _assert_drift_handled(env):
    env.terminate.assert_called_once()
    assert drift_module.get_drift_exit_code() == 1
    assert env.event.is_set() is True


class TestAssertGpuListUnchanged:

    def test_same_list_is_noop(self, env):
        env.cap.assert_gpu_list_unchanged()

        env.detect.assert_called_once()
        _assert_no_action(env)

    @pytest.mark.parametrize('detected, expected', [
        ([(RTX['id'], 'OPTIX'), ('HIP_OTHER_0000:04:00', 'HIP')],
         "cards gone: [HIP 'HIP_AMD_0000:03:00']; cards new: [HIP 'HIP_OTHER_0000:04:00']"),
        ([AMD_PAIR, RTX_PAIR], 'order changed'),
        ([('CUDA_RTX_0000:01:00', 'CUDA'), AMD_PAIR],
         "backend or id changed: [OPTIX 'CUDA_RTX_0000:01:00_OptiX' -> CUDA 'CUDA_RTX_0000:01:00']"),
        ([RTX_PAIR], "cards gone: [HIP 'HIP_AMD_0000:03:00']"),
        ([RTX_PAIR, AMD_PAIR, ('HIP_NEW_0000:05:00', 'HIP')], "cards new: [HIP 'HIP_NEW_0000:05:00']"),
        ([], "cards gone: [OPTIX 'CUDA_RTX_0000:01:00_OptiX', HIP 'HIP_AMD_0000:03:00']"),
    ], ids=['same-count-other-id', 'reordered', 'backend-changed', 'card-gone', 'card-added', 'all-gone'])
    def test_any_difference_fires_drift_handler(self, env, caplog, detected, expected):
        env.detect.return_value = detected

        with caplog.at_level(logging.CRITICAL, logger=SLOTS):
            env.cap.assert_gpu_list_unchanged()

        _assert_drift_handled(env)
        message = next(r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL)
        assert message.startswith('GPU change detected: ')
        assert expected in message
        assert 'in use [OPTIX' in message and 'detected now [' in message

    def test_drift_handler_sets_exit_code_via_slots_helper(self, env, mocker):
        env.detect.return_value = [RTX_PAIR]
        exit_spy = mocker.patch(f'{SLOTS}.set_drift_exit_code', wraps=drift_module.set_drift_exit_code)

        env.cap.assert_gpu_list_unchanged()

        exit_spy.assert_called_once_with(1)

    def test_termination_error_still_signals_shutdown(self, env):
        env.detect.return_value = []
        env.terminate.side_effect = RuntimeError('manager unreachable')

        env.cap.assert_gpu_list_unchanged()

        assert drift_module.get_drift_exit_code() == 1
        assert env.event.is_set() is True

    def test_capacity_module_never_calls_sys_exit(self, env):
        """Belt-and-suspenders guard: sys.exit would abort the test run."""
        env.detect.return_value = [AMD_PAIR]
        env.cap.assert_gpu_list_unchanged()
        # Reaching this line means no sys.exit; the exit code is only flagged.
        assert drift_module.get_drift_exit_code() == 1

    def test_cpu_only_worker_skips_detection_entirely(self, env):
        """No card at startup: the 5-15s detection never runs. Issue #49."""
        WorkerCapacity(_profile(startup_gpu_count=0)).assert_gpu_list_unchanged()

        env.detect.assert_not_called()
        _assert_no_action(env)

    def test_detection_failure_is_noop_not_drift(self, env):
        """None (subprocess failure) is 'try again later'. Issue #49."""
        env.detect.return_value = None

        env.cap.assert_gpu_list_unchanged()

        _assert_no_action(env)

    def test_non_ascii_ids_are_logged_ascii_safe(self, env, caplog):
        env.detect.return_value = [RTX_PAIR, ('HIP_显卡®', 'HIP')]

        with caplog.at_level(logging.CRITICAL, logger=SLOTS):
            env.cap.assert_gpu_list_unchanged()

        message = caplog.records[-1].getMessage()
        message.encode('ascii')
        assert ascii('HIP_显卡®') in message


class TestDriftExitCodeHelpers:

    def test_initial_value_is_none(self):
        assert drift_module.get_drift_exit_code() is None

    def test_set_and_get(self):
        drift_module.set_drift_exit_code(1)
        assert drift_module.get_drift_exit_code() == 1

    def test_reset(self):
        drift_module.set_drift_exit_code(1)
        drift_module.reset_drift_exit_code()
        assert drift_module.get_drift_exit_code() is None


@pytest.fixture
def fake_blender(mocker, monkeypatch, tmp_path):
    """The Blender that filled the cached list exists; subprocess.run is mocked."""
    exe = tmp_path / 'blender.exe'
    exe.write_text('')
    monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', str(exe))
    return mocker.patch('sethlans_worker_agent.capacity.drift.subprocess.run')


class TestDetectPhysicalGpusNow:

    def test_returns_none_when_no_detection_blender_is_known(self, monkeypatch):
        """No remembered Blender is a skip signal, not 'no GPUs'."""
        monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', None)
        assert drift_module.detect_physical_gpus_now() is None

    def test_returns_preferred_card_pairs_in_order(self, fake_blender, mocker):
        raw = [
            {'index': 0, 'name': 'NVIDIA GeForce RTX 4080', 'type': 'CUDA', 'id': 'CUDA_R'},
            {'index': 1, 'name': 'AMD Radeon', 'type': 'HIP', 'id': 'HIP_A'},
            {'index': 2, 'name': 'NVIDIA GeForce RTX 4080', 'type': 'OPTIX', 'id': 'CUDA_R_OptiX'},
        ]
        fake_blender.return_value = mocker.MagicMock(stdout=f'preamble\n{json.dumps(raw)}\ntrailing\n')
        details_before = hardware_detection._gpu_details_cache
        devices_before = hardware_detection._gpu_devices_cache

        # _filter_preferred_gpus orders cards by the chosen entry's index.
        assert drift_module.detect_physical_gpus_now() == [('HIP_A', 'HIP'), ('CUDA_R_OptiX', 'OPTIX')]
        # The caches the worker runs on are neither read nor written.
        assert hardware_detection._gpu_details_cache is details_before
        assert hardware_detection._gpu_devices_cache is devices_before

    def test_empty_device_list_is_an_empty_list_not_none(self, fake_blender, mocker):
        fake_blender.return_value = mocker.MagicMock(stdout='[]\n')
        assert drift_module.detect_physical_gpus_now() == []

    @pytest.mark.parametrize('error', [
        subprocess.CalledProcessError(1, 'blender'),
        subprocess.TimeoutExpired('blender', 90),
    ], ids=['called-process-error', 'timeout'])
    def test_returns_none_on_subprocess_failure(self, fake_blender, error):
        """A failing or hanging Blender is a skip signal, never drift."""
        fake_blender.side_effect = error
        assert drift_module.detect_physical_gpus_now() is None

    def test_returns_none_when_output_unparseable(self, fake_blender, mocker):
        fake_blender.return_value = mocker.MagicMock(stdout='garbage output with no json')
        assert drift_module.detect_physical_gpus_now() is None
