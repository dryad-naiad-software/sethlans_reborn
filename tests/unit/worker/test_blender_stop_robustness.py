# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Robustness of stopping Blender (#246, #247).

A killed render is never a success (on POSIX psutil reaps the child, so
``Popen.wait()`` returns 0); a render that already finished is not stopped;
a render that ends on its own during a shutdown without a usable result is
handed back; Blender runs in its own process group and its whole tree is
still stopped; an unkillable process keeps its slot and the job is still
handed back; a psutil error while stopping is still a stop; nothing starts
once the shutdown has begun.
"""
import subprocess
import sys
import threading

import psutil

from sethlans_worker_agent import blender_executor, blender_stop, blender_thumbnail, job_processor, job_report
from sethlans_worker_agent.executors import ExecutionContext, RendererReservation
from sethlans_worker_agent.executors.blender import BlenderExecutor
from tests.unit.worker import test_blender_executor_gpu_check as _gpu_check
from tests.unit.worker import test_blender_stop as _stop_tests

_REAL_POPEN = subprocess.Popen  # saved before any test patches subprocess.Popen

# Shared fixtures and helpers of the stop tests.
executor_env = _gpu_check.executor_env
loop = _stop_tests.loop
_cpu_job = _stop_tests._cpu_job


def _run_through_report(job, shutdown, mocker):
    """Execute ``job`` through the Blender adapter and report the result."""
    hand_back = mocker.patch('sethlans_worker_agent.blender_hand_back.hand_back')
    update = mocker.patch('sethlans_worker_agent.api_handler.update_job_status')
    upload = mocker.patch('sethlans_worker_agent.api_handler.upload_render_output')
    result = BlenderExecutor().execute(job, ExecutionContext(
        reservation=None, yield_monitor_factory=None, manual_stop_event=None, shutdown_event=shutdown))
    job_report.report_result(job, result, job_processor)
    return result, hand_back, update, upload


_GROUP_KEYS = ('creationflags', 'start_new_session')


class TestKilledRenderIsNeverASuccess:
    """On POSIX psutil reaps the killed child, so ``Popen.wait()`` returns 0.

    The stop is decided by the job's stop mark, never by the exit code. The
    real-child test runs on every platform (a killed child's exit code is 0
    only on Linux and macOS); the mocked one returns 0 everywhere.
    """

    def test_real_child_killed_on_shutdown_is_handed_back_not_done(self, mocker, executor_env):
        executor_env['popen'].side_effect = lambda cmd, **kw: _REAL_POPEN(
            [sys.executable, '-c', 'import time; time.sleep(60)'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8',
            **{k: v for k, v in kw.items() if k in _GROUP_KEYS})
        mocker.patch('sethlans_worker_agent.blender_stop.stopped_before_start', return_value=False)
        shutdown = threading.Event()
        shutdown.set()

        result, hand_back, update, upload = _run_through_report(_cpu_job(), shutdown, mocker)

        assert result.success is False and result.stopped is True
        assert result.stop_reason == blender_stop.WORKER_SHUTDOWN
        hand_back.assert_called_once_with(238, blender_stop.WORKER_SHUTDOWN)
        upload.assert_not_called()
        update.assert_not_called()

    def test_killed_render_with_exit_code_0_is_handed_back(self, loop, mocker):
        proc, _terminate, _assignment = loop
        proc.wait.return_value = 0  # what Popen.wait() gives after psutil reaped the child
        shutdown = threading.Event()
        shutdown.set()

        result, hand_back, update, upload = _run_through_report(_cpu_job(), shutdown, mocker)

        assert result.success is False and result.stopped is True
        hand_back.assert_called_once_with(238, blender_stop.WORKER_SHUTDOWN)
        update.assert_not_called()
        upload.assert_not_called()

    def test_render_already_finished_is_not_stopped(self, mocker):
        proc = mocker.Mock(pid=1)
        proc.poll.return_value = 0
        shutdown = threading.Event()
        shutdown.set()
        job = {}

        assert blender_stop.stop_on_shutdown(shutdown, proc, 1, job) is False
        mocker.patch('sethlans_worker_agent.api_extras.get_job_assignment', return_value=('QUEUED', None))
        assert blender_stop.check_manager(1, proc, job) is None
        assert job == {}


class TestStopRobustness:

    def test_unkillable_process_keeps_its_slot_and_still_hands_back(self, loop, mocker, caplog):
        proc, _terminate, _assignment = loop

        def wait(timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired('blender', timeout)
            return 1
        proc.wait.side_effect = wait
        job = dict(_cpu_job(), _renderer_reservation=RendererReservation())
        mocker.patch('sethlans_worker_agent.blender_stop.stopped_before_start', return_value=False)
        shutdown = threading.Event()
        shutdown.set()

        result, hand_back, update, _upload = _run_through_report(job, shutdown, mocker)

        assert result.stopped is True and result.success is False
        assert job['_renderer_reservation'].retained is True
        assert 'did not exit' in caplog.text
        hand_back.assert_called_once_with(238, blender_stop.WORKER_SHUTDOWN)
        update.assert_not_called()

    def test_access_denied_while_stopping_is_still_a_stop(self, loop, mocker):
        _proc, terminate, _assignment = loop
        terminate.side_effect = psutil.AccessDenied(4242)
        mocker.patch('sethlans_worker_agent.blender_stop.stopped_before_start', return_value=False)
        shutdown = threading.Event()
        shutdown.set()
        job = _cpu_job()

        result = blender_executor.execute_blender_job(job, shutdown_event=shutdown)

        assert result[0] is False and result[5] == blender_stop.STOPPED_MESSAGE
        assert job[blender_stop.STOPPED_KEY] == blender_stop.WORKER_SHUTDOWN


class TestNothingStartsDuringShutdown:

    def test_render_is_not_started(self, mocker, executor_env):
        shutdown = threading.Event()
        shutdown.set()

        result, hand_back, update, _upload = _run_through_report(_cpu_job(), shutdown, mocker)

        executor_env['popen'].assert_not_called()
        executor_env['acquire'].assert_not_called()
        assert result.stopped is True and result.error_message == blender_stop.STOPPED_MESSAGE
        hand_back.assert_called_once_with(238, blender_stop.WORKER_SHUTDOWN)
        update.assert_not_called()

    def test_thumbnail_pass_is_not_started(self, mocker, tmp_path):
        popen = mocker.patch('subprocess.Popen')
        stop = threading.Event()
        stop.set()

        result = blender_thumbnail.render_exr_hdr_thumbnail(
            _cpu_job(), 'blender', str(tmp_path / 'scene.blend'), 1,
            str(tmp_path / 'render_####.exr'), None, stop_event=stop,
        )

        assert result is None
        popen.assert_not_called()

    def test_no_claim_after_the_hardware_check_started_the_shutdown(self, mocker):
        from sethlans_worker_agent import agent_loop
        from tests.unit.worker.test_agent_polling_gate import TestPollingGate, agent_module
        TestPollingGate()._patch_common(mocker)
        shutdown = threading.Event()
        mocker.patch.object(agent_module, '_shutdown_event', shutdown)
        mocker.patch('sethlans_worker_agent.job_processor.maybe_assert_gpu_list_unchanged',
                     side_effect=shutdown.set)
        claim = mocker.patch('sethlans_worker_agent.job_processor.get_and_claim_job')
        poll = mocker.patch('sethlans_worker_agent.api_handler.poll_for_available_jobs')

        agent_loop._run_loop_iteration(worker_id=1)

        claim.assert_not_called()
        poll.assert_not_called()


class TestEndedDuringShutdown:
    """Blender ended on its own (a signal reached it directly) before the loop stopped it."""

    def _ended(self, loop, return_code, out_exists, tmp_path, mocker):
        proc, terminate, _assignment = loop
        proc.poll.return_value = return_code  # already gone before the first loop check
        proc.wait.return_value = return_code
        mocker.patch('sethlans_worker_agent.config.WORKER_OUTPUT_DIR', str(tmp_path))
        if out_exists:
            (tmp_path / 'render').mkdir()
            (tmp_path / 'render' / 'frame_0001.png').write_bytes(b'png')
        shutdown = threading.Event()
        shutdown.set()
        result, hand_back, update, _upload = _run_through_report(_cpu_job(), shutdown, mocker)
        terminate.assert_not_called()
        return result, hand_back, update

    def test_non_zero_exit_is_handed_back_not_error(self, loop, tmp_path, mocker):
        result, hand_back, update = self._ended(loop, 130, False, tmp_path, mocker)

        assert result.stopped is True
        hand_back.assert_called_once_with(238, blender_stop.WORKER_SHUTDOWN)
        update.assert_not_called()

    def test_exit_0_without_output_is_handed_back_not_done(self, loop, tmp_path, mocker):
        result, hand_back, update = self._ended(loop, 0, False, tmp_path, mocker)

        assert result.stopped is True
        hand_back.assert_called_once()
        update.assert_not_called()

    def test_exit_0_with_its_output_is_reported(self, loop, tmp_path, mocker):
        result, hand_back, update = self._ended(loop, 0, True, tmp_path, mocker)

        assert result.success is True and result.stopped is False
        hand_back.assert_not_called()
        assert update.call_args.args[1]['status'] == 'DONE'


class TestOwnProcessGroup:

    def test_group_kwargs_for_this_platform(self):
        kwargs = blender_stop.popen_group_kwargs()
        if sys.platform == 'win32':
            assert kwargs['creationflags'] & subprocess.CREATE_NEW_PROCESS_GROUP
            assert kwargs['creationflags'] & subprocess.CREATE_NO_WINDOW
        else:
            assert kwargs == {'start_new_session': True}

    def test_tree_stop_still_reaches_a_grandchild(self):
        """Started in its own group/session, the child and its own child are both stopped."""
        script = ('import subprocess, sys, time; '
                  'subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]); time.sleep(60)')
        child = _REAL_POPEN([sys.executable, '-c', script], **blender_stop.popen_group_kwargs())
        grandchildren = []
        try:
            parent = psutil.Process(child.pid)
            for _ in range(200):
                grandchildren = parent.children(recursive=True)
                if grandchildren:
                    break
                threading.Event().wait(0.05)
            assert grandchildren
            event = threading.Event()
            event.set()

            assert blender_stop.stop_on_shutdown(event, child, 1, {}) is True

            assert child.wait(timeout=10) is not None
            _gone, alive = psutil.wait_procs(grandchildren, timeout=10)
            assert alive == []
        finally:
            for proc in grandchildren:
                try:
                    proc.kill()
                except psutil.Error:
                    pass
            if child.poll() is None:
                child.kill()
                child.wait()
