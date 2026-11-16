# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The periodic GPU check keeps running when its detection Blender is removed (#253).

The version sync removes old Blender versions, including the one that
produced the GPU list in use. The check then detects with another installed
Blender: the same list adopts it as the detection Blender, a different list
is a hardware change. A detection that cannot run or whose output cannot be
read is "try again later" (``drift.DETECTION_FAILED``), never an exception
out of the agent loop's check.
"""
import json
import logging
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

_saved_argv = sys.argv
sys.argv = ['agent-test-runner']
try:
    from sethlans_worker_agent import agent as agent_module  # noqa: E402
finally:
    sys.argv = _saved_argv

from sethlans_worker_agent import hardware_detection, job_processor  # noqa: E402
from sethlans_worker_agent.capacity import (  # noqa: E402
    CapacityProfile,
    WorkerCapacity,
    drift as drift_module,
)

DRIFT = drift_module.__name__
HIP = {'index': 0, 'name': 'AMD Radeon', 'type': 'HIP', 'id': 'HIP_A'}
OTHER_HIP = {'index': 0, 'name': 'AMD Radeon', 'type': 'HIP', 'id': 'HIP_B'}


def _capacity(gpu_slot_count=1, startup_gpu_count=1):
    return WorkerCapacity(CapacityProfile(
        gpu_slot_count=gpu_slot_count, cpu_slot_count=1, cpu_thread_ceiling=15,
        cpu_threads_effective=15, startup_gpu_count=startup_gpu_count, gpu_mode='split',
    ))


@pytest.fixture
def env(mocker, monkeypatch, tmp_path):
    """The list in use came from a Blender that is gone; another one is installed."""
    gone, other = tmp_path / 'blender-old.exe', tmp_path / 'blender-new.exe'
    other.write_text('')
    monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', str(gone))
    find = mocker.patch(
        'sethlans_worker_agent.capacity.drift._choose_fallback_blender', return_value=str(other),
    )
    mocker.patch('sethlans_worker_agent.system_monitor.get_gpu_device_details', return_value=[HIP])
    run = mocker.patch(
        'sethlans_worker_agent.capacity.drift.subprocess.run',
        return_value=mocker.MagicMock(stdout=json.dumps([HIP]) + '\n'),
    )
    terminate = mocker.patch('sethlans_worker_agent.job_processor.terminate_all_active_jobs_for_drift')
    event = threading.Event()
    mocker.patch.object(agent_module, '_shutdown_event', event)
    return SimpleNamespace(gone=str(gone), other=str(other), find=find, run=run,
                           terminate=terminate, event=event, cap=_capacity())


def _assert_no_action(env):
    env.terminate.assert_not_called()
    assert drift_module.get_drift_exit_code() is None
    assert env.event.is_set() is False


def _messages(caplog, level, text):
    return [r.getMessage() for r in caplog.records if r.levelno == level and text in r.getMessage()]


class TestRememberedBlenderRemoved:

    def test_same_list_adopts_the_other_blender(self, env, caplog):
        with caplog.at_level(logging.DEBUG, logger=DRIFT):
            assert env.cap.assert_gpu_list_unchanged() is None

        assert env.run.call_args.args[0][0] == env.other
        _assert_no_action(env)
        assert hardware_detection._gpu_details_blender_exe == env.other
        adopted = _messages(caplog, logging.INFO, 'from now on')
        assert len(adopted) == 1
        assert ascii(env.other) in adopted[0]
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

    def test_next_check_uses_the_adopted_blender_directly(self, env, caplog):
        env.cap.assert_gpu_list_unchanged()
        finds_after_first = env.find.call_count
        caplog.clear()

        with caplog.at_level(logging.DEBUG, logger=DRIFT):
            assert env.cap.assert_gpu_list_unchanged() is None

        assert env.run.call_count == 2
        assert env.run.call_args.args[0][0] == env.other
        assert env.find.call_count == finds_after_first  # no fallback search
        assert _messages(caplog, logging.INFO, 'no longer installed') == []
        assert _messages(caplog, logging.INFO, 'from now on') == []
        _assert_no_action(env)

    def test_different_list_is_a_hardware_change_and_nothing_is_adopted(self, env, mocker, caplog):
        env.run.return_value = mocker.MagicMock(stdout=json.dumps([OTHER_HIP]) + '\n')

        with caplog.at_level(logging.DEBUG, logger=DRIFT):
            assert env.cap.assert_gpu_list_unchanged() is None

        assert env.run.call_args.args[0][0] == env.other
        env.terminate.assert_called_once()
        assert drift_module.get_drift_exit_code() == 1
        assert env.event.is_set() is True
        assert hardware_detection._gpu_details_blender_exe == env.gone
        assert _messages(caplog, logging.INFO, 'from now on') == []

    def test_no_blender_installed_tries_again_later(self, env, caplog):
        env.find.return_value = None

        with caplog.at_level(logging.DEBUG, logger=DRIFT):
            for _ in range(3):
                assert env.cap.assert_gpu_list_unchanged() == drift_module.DETECTION_FAILED

        env.run.assert_not_called()
        _assert_no_action(env)
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
        assert hardware_detection._gpu_details_blender_exe == env.gone


DETECTION_ERRORS = [
    OSError(8, 'Exec format error'),
    FileNotFoundError(2, 'No such file or directory'),
    UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid start byte'),
]
DETECTION_ERROR_IDS = ['os-error', 'file-not-found', 'undecodable-output']
# Parseable JSON whose records _filter_preferred_gpus cannot use.
MALFORMED_OUTPUTS = [
    '[1]\n',
    '[{"index": 0, "name": "AMD Radeon", "id": "HIP_A"}]\n',
    '[{"name": "AMD Radeon", "type": "HIP", "id": "HIP_A"}]\n',
    '[{"index": 0, "name": "AMD Radeon", "type": "HIP", "id": 7}]\n',
]
MALFORMED_IDS = ['not-a-dict', 'no-type', 'no-index', 'id-not-a-string']


@pytest.fixture
def remembered(mocker, monkeypatch, tmp_path):
    """The Blender that produced the list in use is installed; returns the run mock."""
    exe = tmp_path / 'blender.exe'
    exe.write_text('')
    monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', str(exe))
    mocker.patch('sethlans_worker_agent.system_monitor.get_gpu_device_details', return_value=[HIP])
    terminate = mocker.patch('sethlans_worker_agent.job_processor.terminate_all_active_jobs_for_drift')
    mocker.patch.object(agent_module, '_shutdown_event', threading.Event())
    return SimpleNamespace(run=mocker.patch('sethlans_worker_agent.capacity.drift.subprocess.run'),
                           terminate=terminate)


class TestDetectionThatCannotRun:

    @pytest.mark.parametrize('error', DETECTION_ERRORS, ids=DETECTION_ERROR_IDS)
    def test_detect_returns_none(self, remembered, error):
        remembered.run.side_effect = error
        assert drift_module.detect_physical_gpus_now() is None

    @pytest.mark.parametrize('error', DETECTION_ERRORS, ids=DETECTION_ERROR_IDS)
    def test_comparison_reports_detection_failed(self, remembered, error):
        remembered.run.side_effect = error

        assert _capacity().assert_gpu_list_unchanged() == drift_module.DETECTION_FAILED

        remembered.terminate.assert_not_called()
        assert drift_module.get_drift_exit_code() is None

    @pytest.mark.parametrize('error', DETECTION_ERRORS, ids=DETECTION_ERROR_IDS)
    def test_agent_loop_check_does_not_raise(self, remembered, mocker, error):
        remembered.run.side_effect = error
        job_processor._capacity = _capacity()
        mocker.patch('sethlans_worker_agent.config.HEARTBEAT_INTERVAL_SECONDS', 30)
        mocker.patch('sethlans_worker_agent.job_processor.time.monotonic', return_value=1030.0)
        job_processor._last_drift_check_ts = 1000.0

        job_processor.maybe_assert_gpu_list_unchanged()

        remembered.run.assert_called_once()
        remembered.terminate.assert_not_called()
        assert drift_module.get_drift_exit_code() is None
        assert job_processor._last_drift_check_ts == 1030.0

    @pytest.mark.parametrize('stdout', MALFORMED_OUTPUTS, ids=MALFORMED_IDS)
    def test_malformed_device_record_tries_again_later(self, remembered, mocker, stdout):
        remembered.run.return_value = mocker.MagicMock(stdout=stdout)

        assert drift_module.detect_physical_gpus_now() is None
        assert _capacity().assert_gpu_list_unchanged() == drift_module.DETECTION_FAILED
        remembered.terminate.assert_not_called()

    def test_output_is_decoded_as_utf8_with_replacement(self, remembered, mocker):
        remembered.run.return_value = mocker.MagicMock(stdout=json.dumps([HIP]) + '\n')

        assert drift_module.detect_physical_gpus_now() == [('HIP_A', 'HIP')]

        kwargs = remembered.run.call_args.kwargs
        assert (kwargs['text'], kwargs['encoding'], kwargs['errors']) == (True, 'utf-8', 'replace')


class TestComparisonOutcome:

    def test_detection_failed_value(self, mocker):
        mocker.patch('sethlans_worker_agent.capacity.slots.detect_physical_gpus_now', return_value=None)
        assert _capacity().assert_gpu_list_unchanged() == drift_module.DETECTION_FAILED

    def test_unchanged_list_returns_none(self, mocker):
        mocker.patch('sethlans_worker_agent.system_monitor.get_gpu_device_details', return_value=[HIP])
        mocker.patch('sethlans_worker_agent.capacity.slots.detect_physical_gpus_now',
                     return_value=[('HIP_A', 'HIP')])
        assert _capacity().assert_gpu_list_unchanged() is None

    @pytest.mark.parametrize('gpu_slots, startup_gpus', [(0, 0), (1, 0), (0, 2)],
                             ids=['no-gpu', 'no-gpu-at-startup', 'force-cpu-only'])
    def test_skipped_by_rule_returns_none_without_detecting(self, mocker, gpu_slots, startup_gpus):
        detect = mocker.patch('sethlans_worker_agent.capacity.slots.detect_physical_gpus_now')
        cap = _capacity(gpu_slot_count=gpu_slots, startup_gpu_count=startup_gpus)

        assert cap.assert_gpu_list_unchanged() is None

        detect.assert_not_called()


def test_subprocess_error_family_still_tries_again_later(remembered):
    """CalledProcessError and TimeoutExpired stay "try again later" (#49)."""
    for error in (subprocess.CalledProcessError(1, 'blender'), subprocess.TimeoutExpired('blender', 90)):
        remembered.run.side_effect = error
        assert _capacity().assert_gpu_list_unchanged() == drift_module.DETECTION_FAILED
