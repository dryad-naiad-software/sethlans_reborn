# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The drift check's fresh detection runs the same Blender that produced the
cached GPU list (#239).

Another Blender version can report a card under a different id or
preferred backend, which would read as a false GPU change. When that
Blender is gone, the check skips ("try again later") and says why once.
"""
import json
import logging
import sys
import threading

import pytest

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

RAW = [{'index': 0, 'name': 'AMD Radeon', 'type': 'HIP', 'id': 'HIP_A'}]


@pytest.fixture
def blenders(tmp_path, mocker):
    """Two installed Blenders; the scan's "first" one is the newer."""
    startup, newer = tmp_path / 'blender-startup.exe', tmp_path / 'blender-newer.exe'
    startup.write_text('')
    newer.write_text('')
    mocker.patch(
        'sethlans_worker_agent.hardware_detection._find_any_blender_executable',
        return_value=str(newer),
    )
    return startup, newer


def _detection_output(mocker, devices=RAW):
    return mocker.MagicMock(stdout=json.dumps(devices) + '\n')


class TestRememberedExecutable:

    def test_startup_detection_remembers_its_blender(self, mocker, monkeypatch, blenders):
        _startup, newer = blenders
        monkeypatch.setattr(hardware_detection, '_gpu_details_cache', None)
        monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', None)
        mocker.patch(
            'sethlans_worker_agent.hardware_detection.subprocess.run',
            return_value=_detection_output(mocker),
        )

        assert hardware_detection.get_gpu_device_details() == RAW
        assert hardware_detection._gpu_details_blender_exe == str(newer)

    def test_failed_startup_detection_remembers_nothing(self, mocker, monkeypatch, blenders):
        monkeypatch.setattr(hardware_detection, '_gpu_details_cache', None)
        monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', None)
        mocker.patch(
            'sethlans_worker_agent.hardware_detection.subprocess.run',
            return_value=mocker.MagicMock(stdout='no json'),
        )

        assert hardware_detection.get_gpu_device_details() == []
        assert hardware_detection._gpu_details_blender_exe is None

    def test_fresh_detection_runs_the_remembered_blender(self, mocker, monkeypatch, blenders):
        startup, newer = blenders
        monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', str(startup))
        run = mocker.patch(
            'sethlans_worker_agent.capacity.drift.subprocess.run',
            return_value=_detection_output(mocker),
        )

        assert drift_module.detect_physical_gpus_now() == [('HIP_A', 'HIP')]
        command = run.call_args.args[0]
        assert command[0] == str(startup)
        assert str(newer) not in command


class TestRememberedExecutableMissing:

    def test_missing_blender_skips_without_drift(self, mocker, monkeypatch, tmp_path):
        monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', str(tmp_path / 'gone.exe'))
        mocker.patch(
            'sethlans_worker_agent.system_monitor.get_gpu_device_details',
            return_value=[dict(RAW[0])],
        )
        run = mocker.patch('sethlans_worker_agent.capacity.drift.subprocess.run')
        terminate = mocker.patch('sethlans_worker_agent.job_processor.terminate_all_active_jobs_for_drift')
        event = threading.Event()
        mocker.patch.object(agent_module, '_shutdown_event', event)
        cap = WorkerCapacity(CapacityProfile(
            gpu_slot_count=1, cpu_slot_count=1, cpu_thread_ceiling=15,
            cpu_threads_effective=15, startup_gpu_count=1, gpu_mode='split',
        ))

        cap.assert_gpu_list_unchanged()

        run.assert_not_called()
        terminate.assert_not_called()
        assert drift_module.get_drift_exit_code() is None
        assert event.is_set() is False

    def test_no_recorded_blender_warns_once(self, monkeypatch, caplog):
        monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', None)

        with caplog.at_level(logging.DEBUG, logger=drift_module.__name__):
            for _ in range(3):
                assert drift_module._startup_detection_executable() is None

        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1

    def test_warning_once_per_occurrence(self, monkeypatch, tmp_path, caplog):
        exe = tmp_path / 'blender.exe'
        monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', str(exe))
        logger_name = drift_module.__name__

        def warnings_after(calls):
            with caplog.at_level(logging.DEBUG, logger=logger_name):
                for _ in range(calls):
                    result = drift_module._startup_detection_executable()
                    assert result == (str(exe) if exe.exists() else None)
            found = [r for r in caplog.records if r.levelno == logging.WARNING]
            caplog.clear()
            return found

        assert len(warnings_after(3)) == 1  # missing: one warning, then quiet
        exe.write_text('')
        assert warnings_after(1) == []  # back: no warning
        exe.unlink()
        missing = warnings_after(2)
        assert len(missing) == 1  # missing again: a new occurrence
        assert ascii(str(exe)) in missing[0].getMessage()
