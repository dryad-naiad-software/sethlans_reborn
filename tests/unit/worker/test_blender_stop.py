# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Stopping a Blender render the worker should no longer run (#246, #247).

The executor loop stops the process tree when the worker shuts down (reason
``worker_shutdown``, or ``worker_hardware_changed`` after the drift handler
marked the hardware change) and when the manager's job record shows the job
is no longer this worker's. A failed poll never stops a render; a user
cancel still ends as before. The thumbnail pass stops on shutdown too.
"""
import subprocess
import sys
import threading

import pytest

from sethlans_worker_agent import blender_executor, blender_stop, blender_thumbnail, system_monitor
from tests.unit.worker import test_blender_executor_gpu_check as _gpu_check

# The patched executor environment of the #238 executor tests.
executor_env = _gpu_check.executor_env
OWN_ID, OTHER_ID = 3, 9


def _cpu_job():
    return dict(_gpu_check._gpu_job(), render_device='CPU')


@pytest.fixture
def loop(mocker, executor_env):
    """A render that runs until its tree is terminated, polled without sleeping."""
    proc = _gpu_check._fake_process(mocker, None, [])
    proc.wait.return_value = 1
    executor_env['popen'].return_value = proc
    terminate = mocker.patch('sethlans_worker_agent.blender_stop.terminate_process_tree',
                             side_effect=lambda pid, job_id: setattr(proc.poll, 'return_value', 1))
    assignment = mocker.patch('sethlans_worker_agent.api_extras.get_job_assignment',
                              return_value=('RENDERING', OWN_ID))
    mocker.patch('sethlans_worker_agent.blender_executor.time.sleep')
    mocker.patch.object(system_monitor, 'WORKER_ID', OWN_ID)
    # Let Blender start even though the tests set the shutdown event up front.
    mocker.patch('sethlans_worker_agent.blender_stop.stopped_before_start', return_value=False)
    return proc, terminate, assignment


class TestShutdown:

    @pytest.mark.parametrize('hardware, reason', [
        (False, blender_stop.WORKER_SHUTDOWN), (True, blender_stop.WORKER_HARDWARE_CHANGED),
    ])
    def test_shutdown_stops_the_tree_with_its_reason(self, loop, executor_env, tmp_path, hardware, reason):
        proc, terminate, assignment = loop
        if hardware:
            blender_stop.mark_hardware_change()
        shutdown = threading.Event()
        shutdown.set()
        job = _cpu_job()

        result = blender_executor.execute_blender_job(job, shutdown_event=shutdown)

        terminate.assert_called_once_with(proc.pid, 238)
        assignment.assert_not_called()  # stopped before asking the manager anything
        assert result[0] is False and result[1] is False and result[6] is None
        assert job[blender_stop.STOPPED_KEY] == reason
        executor_env['release'].assert_called_once_with('4.1.1')  # version reference released
        assert list((tmp_path / 'temp').glob('*.py')) == []  # temp script removed


class TestManagerPoll:

    @pytest.mark.parametrize('state', [('QUEUED', None), ('RENDERING', OTHER_ID), ('DONE', OTHER_ID)],
                             ids=['requeued', 'other-worker', 'done-elsewhere'])
    def test_job_no_longer_ours_stops_silently(self, loop, caplog, state):
        proc, terminate, assignment = loop
        assignment.return_value = state
        job = _cpu_job()

        result = blender_executor.execute_blender_job(job, shutdown_event=threading.Event())

        terminate.assert_called_once_with(proc.pid, 238)
        assert result[1] is False  # not a user cancel
        assert job[blender_stop.STOPPED_KEY] == blender_stop.NOT_ASSIGNED
        assert 'no longer assigns this job to this worker' in caplog.text
        assert 'Cancellation received' not in caplog.text

    def test_user_cancel_is_unchanged(self, loop, caplog):
        proc, terminate, assignment = loop
        assignment.return_value = ('CANCELED', OWN_ID)
        job = _cpu_job()

        result = blender_executor.execute_blender_job(job, shutdown_event=threading.Event())

        terminate.assert_called_once_with(proc.pid, 238)
        assert result[1] is True and result[5] == 'Job was canceled by user request.'
        assert blender_stop.STOPPED_KEY not in job
        assert 'Cancellation received' in caplog.text

    @pytest.mark.parametrize('state', [None, ('RENDERING', OWN_ID)], ids=['poll-failed', 'still-ours'])
    def test_render_continues(self, loop, state):
        proc, terminate, assignment = loop
        assignment.return_value = state
        proc.poll.side_effect = lambda: 0 if assignment.call_count >= 2 else None  # two polls, then done
        proc.wait.return_value = 0
        job = _cpu_job()

        result = blender_executor.execute_blender_job(job, shutdown_event=threading.Event())

        terminate.assert_not_called()
        assert assignment.call_count == 2
        assert result[0] is True
        assert blender_stop.STOPPED_KEY not in job


class TestRealProcessTree:

    def test_stop_on_shutdown_terminates_a_running_process(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        try:
            event = threading.Event()
            job = {}
            assert blender_stop.stop_on_shutdown(event, child, 1, job) is False
            event.set()

            assert blender_stop.stop_on_shutdown(event, child, 1, job) is True
            assert child.wait(timeout=10) is not None
            assert job[blender_stop.STOPPED_KEY] == blender_stop.WORKER_SHUTDOWN
            assert job[blender_stop.STARTED_AT_KEY] > 0
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


class TestThumbnailPass:

    def test_stop_during_the_thumbnail_pass(self, mocker, tmp_path):
        mocker.patch('sethlans_worker_agent.config.WORKER_TEMP_DIR', str(tmp_path / 'temp'))
        mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=[])
        stop = threading.Event()
        proc = _gpu_check._fake_process(mocker, None, [])

        def wait(timeout=None):
            stop.set()  # the shutdown arrives while the thumbnail renders
            raise subprocess.TimeoutExpired('blender', timeout)
        proc.wait.side_effect = wait
        mocker.patch('subprocess.Popen', return_value=proc)
        terminate = mocker.patch('sethlans_worker_agent.blender_stop.terminate_process_tree')
        (tmp_path / 'out').mkdir()
        partial = tmp_path / 'out' / 'thumb_render_0001.png'
        partial.write_bytes(b'half')

        result = blender_thumbnail.render_exr_hdr_thumbnail(
            _cpu_job(), 'blender', str(tmp_path / 'scene.blend'), 1,
            str(tmp_path / 'out' / 'render_####.exr'), None, stop_event=stop,
        )

        assert result is None
        terminate.assert_called_once_with(proc.pid, 238)
        assert not partial.exists()
        assert list((tmp_path / 'temp').glob('*.py')) == []

    def test_wait_or_stop_returns_the_exit_code(self, mocker):
        proc = mocker.Mock()
        proc.wait.side_effect = [subprocess.TimeoutExpired('b', 1), 0]

        assert blender_stop.wait_or_stop(proc, threading.Event(), 1, poll_seconds=0) == 0


class TestHeartbeat:

    def test_periodic_heartbeat_carries_the_boot_id(self, mocker):
        from sethlans_worker_agent import boot_id
        from tests.unit.worker.test_system_monitor_setup_complete import _patch_common
        _patch_common(mocker)
        mocker.patch.object(system_monitor, 'WORKER_ID', 7)
        send = mocker.patch('sethlans_worker_agent.api_handler.send_authenticated_heartbeat',
                            return_value={'id': 7})

        system_monitor.send_heartbeat(is_busy=True, active_jobs={})

        assert send.call_args.args[0]['worker_boot_id'] == boot_id.BOOT_ID


class TestBoundedWaitAndEarlyChecks:

    def test_user_cancel_wait_is_bounded_too(self, loop, mocker):
        proc, _terminate, assignment = loop
        assignment.return_value = ('CANCELED', OWN_ID)

        def wait(timeout=None):
            if timeout is None:
                raise AssertionError('unbounded wait after a kill')
            raise subprocess.TimeoutExpired('blender', timeout)
        proc.wait.side_effect = wait
        from sethlans_worker_agent.executors import RendererReservation
        job = dict(_cpu_job(), _renderer_reservation=RendererReservation())

        result = blender_executor.execute_blender_job(job, shutdown_event=threading.Event())

        assert result[1] is True  # still a user cancel
        assert job['_renderer_reservation'].retained is True

    def test_shutdown_before_the_downloads_starts_nothing(self, mocker, executor_env):
        asset = mocker.patch('sethlans_worker_agent.asset_manager.ensure_asset_is_available')
        version = mocker.patch(
            'sethlans_worker_agent.tool_manager.tool_manager_instance.ensure_blender_version_available')
        shutdown = threading.Event()
        shutdown.set()
        job = _cpu_job()

        result = blender_executor.execute_blender_job(job, shutdown_event=shutdown)

        asset.assert_not_called()
        version.assert_not_called()
        executor_env['popen'].assert_not_called()
        assert result[5] == blender_stop.STOPPED_MESSAGE
        assert job[blender_stop.STOPPED_KEY] == blender_stop.WORKER_SHUTDOWN
