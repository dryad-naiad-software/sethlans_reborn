# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the GPU drift detector.

Covers FR-28: same count is a no-op; increased/decreased counts fire the
drift handler, terminate active jobs, set the module-level exit-code
flag, and set agent._shutdown_event. sys.exit is NEVER called from
inside the capacity module (FR-22a).
"""
import sys
import threading

# agent.py runs argparse at import time; stub argv to avoid consuming
# pytest's command line.
_saved_argv = sys.argv
sys.argv = ['agent-test-runner']
try:
    from sethlans_worker_agent import agent as agent_module  # noqa: E402
finally:
    sys.argv = _saved_argv

from sethlans_worker_agent.capacity import (  # noqa: E402
    CapacityProfile,
    WorkerCapacity,
    drift as drift_module,
)


def _profile(startup_gpu_count=2):
    return CapacityProfile(
        gpu_slot_count=startup_gpu_count,
        cpu_slot_count=1,
        cpu_thread_ceiling=15,
        cpu_threads_effective=15,
        startup_gpu_count=startup_gpu_count,
        gpu_mode='split',
    )


class TestAssertGpuCountUnchanged:

    def test_same_count_is_noop(self, mocker):
        cap = WorkerCapacity(_profile(startup_gpu_count=2))
        mocker.patch(
            'sethlans_worker_agent.capacity.slots.count_physical_gpus_now',
            return_value=2,
        )
        terminate_spy = mocker.patch(
            'sethlans_worker_agent.job_processor.'
            'terminate_all_active_jobs_for_drift'
        )
        # Replace agent._shutdown_event with a fresh event we can inspect
        # without affecting real global state.
        fresh_event = threading.Event()
        mocker.patch.object(agent_module, '_shutdown_event', fresh_event)

        cap.assert_gpu_count_unchanged()
        terminate_spy.assert_not_called()
        assert drift_module.get_drift_exit_code() is None
        assert fresh_event.is_set() is False

    def test_increased_count_fires_drift_handler(self, mocker):
        cap = WorkerCapacity(_profile(startup_gpu_count=2))
        mocker.patch(
            'sethlans_worker_agent.capacity.slots.count_physical_gpus_now',
            return_value=3,
        )
        terminate_spy = mocker.patch(
            'sethlans_worker_agent.job_processor.'
            'terminate_all_active_jobs_for_drift'
        )
        # Make sure the capacity module NEVER calls sys.exit.
        exit_spy = mocker.patch(
            'sethlans_worker_agent.capacity.slots.set_drift_exit_code',
            wraps=drift_module.set_drift_exit_code,
        )
        fresh_event = threading.Event()
        mocker.patch.object(agent_module, '_shutdown_event', fresh_event)

        cap.assert_gpu_count_unchanged()

        terminate_spy.assert_called_once()
        exit_spy.assert_called_once_with(1)
        assert drift_module.get_drift_exit_code() == 1
        assert fresh_event.is_set() is True

    def test_decreased_count_fires_drift_handler(self, mocker):
        cap = WorkerCapacity(_profile(startup_gpu_count=2))
        mocker.patch(
            'sethlans_worker_agent.capacity.slots.count_physical_gpus_now',
            return_value=1,
        )
        terminate_spy = mocker.patch(
            'sethlans_worker_agent.job_processor.'
            'terminate_all_active_jobs_for_drift'
        )
        fresh_event = threading.Event()
        mocker.patch.object(agent_module, '_shutdown_event', fresh_event)

        cap.assert_gpu_count_unchanged()

        terminate_spy.assert_called_once()
        assert drift_module.get_drift_exit_code() == 1
        assert fresh_event.is_set() is True

    def test_termination_error_still_signals_shutdown(self, mocker):
        cap = WorkerCapacity(_profile(startup_gpu_count=2))
        mocker.patch(
            'sethlans_worker_agent.capacity.slots.count_physical_gpus_now',
            return_value=0,
        )
        mocker.patch(
            'sethlans_worker_agent.job_processor.'
            'terminate_all_active_jobs_for_drift',
            side_effect=RuntimeError('manager unreachable'),
        )
        fresh_event = threading.Event()
        mocker.patch.object(agent_module, '_shutdown_event', fresh_event)

        # try/finally in assert_gpu_count_unchanged must swallow the
        # exception and still set the exit code + shutdown event.
        cap.assert_gpu_count_unchanged()
        assert drift_module.get_drift_exit_code() == 1
        assert fresh_event.is_set() is True

    def test_capacity_module_never_calls_sys_exit(self, mocker):
        """Belt-and-suspenders guard against future regressions."""
        cap = WorkerCapacity(_profile(startup_gpu_count=2))
        mocker.patch(
            'sethlans_worker_agent.capacity.slots.count_physical_gpus_now',
            return_value=5,
        )
        mocker.patch(
            'sethlans_worker_agent.job_processor.'
            'terminate_all_active_jobs_for_drift'
        )
        fresh_event = threading.Event()
        mocker.patch.object(agent_module, '_shutdown_event', fresh_event)
        # Intentionally don't patch sys.exit; if the module ever calls it
        # the test process would abort. Run and verify normal return.
        cap.assert_gpu_count_unchanged()

    def test_cpu_only_worker_skips_detection_entirely(self, mocker):
        """CPU-only workers (startup_gpu_count=0) must early-return
        without invoking the 5-15s detection subprocess. Issue #49.
        """
        cap = WorkerCapacity(_profile(startup_gpu_count=0))
        count_spy = mocker.patch(
            'sethlans_worker_agent.capacity.slots.count_physical_gpus_now',
        )
        terminate_spy = mocker.patch(
            'sethlans_worker_agent.job_processor.'
            'terminate_all_active_jobs_for_drift'
        )
        fresh_event = threading.Event()
        mocker.patch.object(agent_module, '_shutdown_event', fresh_event)

        cap.assert_gpu_count_unchanged()

        count_spy.assert_not_called()
        terminate_spy.assert_not_called()
        assert drift_module.get_drift_exit_code() is None
        assert fresh_event.is_set() is False

    def test_none_from_count_is_noop_not_drift(self, mocker):
        """count_physical_gpus_now() returning None (subprocess failure)
        must NOT trigger the drift handler. Issue #49 — transient
        Blender hangs should not self-evict the worker.
        """
        cap = WorkerCapacity(_profile(startup_gpu_count=2))
        mocker.patch(
            'sethlans_worker_agent.capacity.slots.count_physical_gpus_now',
            return_value=None,
        )
        terminate_spy = mocker.patch(
            'sethlans_worker_agent.job_processor.'
            'terminate_all_active_jobs_for_drift'
        )
        fresh_event = threading.Event()
        mocker.patch.object(agent_module, '_shutdown_event', fresh_event)

        cap.assert_gpu_count_unchanged()

        terminate_spy.assert_not_called()
        assert drift_module.get_drift_exit_code() is None
        assert fresh_event.is_set() is False


# --- drift module exit-code helpers ---

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


# --- count_physical_gpus_now ---

class TestCountPhysicalGpusNow:

    def test_returns_none_when_no_blender_executable(self, mocker):
        """Missing Blender → skip signal, not a false-zero drift trigger."""
        mocker.patch(
            'sethlans_worker_agent.hardware_detection.'
            '_find_any_blender_executable',
            return_value=None,
        )
        assert drift_module.count_physical_gpus_now() is None

    def test_parses_device_list_from_stdout(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.hardware_detection.'
            '_find_any_blender_executable',
            return_value='/fake/blender',
        )
        mocker.patch(
            'sethlans_worker_agent.hardware_detection._filter_preferred_gpus',
            return_value=[{'name': 'A'}, {'name': 'B'}],
        )
        fake_result = mocker.MagicMock()
        fake_result.stdout = (
            'preamble line\n'
            '[{"name": "A"}, {"name": "B"}]\n'
            'trailing line\n'
        )
        mocker.patch(
            'sethlans_worker_agent.capacity.drift.subprocess.run',
            return_value=fake_result,
        )
        assert drift_module.count_physical_gpus_now() == 2

    def test_returns_none_on_subprocess_error(self, mocker):
        """CalledProcessError → skip signal. Must NOT be interpreted as drift."""
        import subprocess
        mocker.patch(
            'sethlans_worker_agent.hardware_detection.'
            '_find_any_blender_executable',
            return_value='/fake/blender',
        )
        mocker.patch(
            'sethlans_worker_agent.capacity.drift.subprocess.run',
            side_effect=subprocess.CalledProcessError(1, 'blender'),
        )
        assert drift_module.count_physical_gpus_now() is None

    def test_returns_none_on_subprocess_timeout(self, mocker):
        """TimeoutExpired → skip signal. A transient Blender hang must
        not fire a false drift and self-evict the worker."""
        import subprocess
        mocker.patch(
            'sethlans_worker_agent.hardware_detection.'
            '_find_any_blender_executable',
            return_value='/fake/blender',
        )
        mocker.patch(
            'sethlans_worker_agent.capacity.drift.subprocess.run',
            side_effect=subprocess.TimeoutExpired('blender', 90),
        )
        assert drift_module.count_physical_gpus_now() is None

    def test_returns_none_when_output_unparseable(self, mocker):
        """No JSON line in stdout → skip signal (not 0)."""
        mocker.patch(
            'sethlans_worker_agent.hardware_detection.'
            '_find_any_blender_executable',
            return_value='/fake/blender',
        )
        fake_result = mocker.MagicMock()
        fake_result.stdout = 'garbage output with no json'
        mocker.patch(
            'sethlans_worker_agent.capacity.drift.subprocess.run',
            return_value=fake_result,
        )
        assert drift_module.count_physical_gpus_now() is None
