# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-job stop of a Blender job whose card can no longer be used (FR-CL-5, C3, AC-8).

The job's own stop event is seen wherever the shutdown event is: before the
download and before the render (``blender_executor``), during the render
(``stop_on_shutdown``, test_blender_stop.py), after it (``mark_if_cut_short``),
in the yield abort check (``blender_yield.should_abort``) and in the
thumbnail pass. The reason is ``worker_hardware_changed``; the shutdown event
is never set. The registry is claim-scoped and emptied on release.
"""
import subprocess
import threading

import pytest

from sethlans_worker_agent import blender_executor, blender_stop, blender_thumbnail, blender_yield
from sethlans_worker_agent import job_claim, job_processor, job_resources
from sethlans_worker_agent.blender_job_stop import registered_ids_for_tests as registered_ids
from tests.unit.worker import test_blender_executor_gpu_check as _gpu_check
from tests.unit.worker.executors._fakes import blender_job, unreal_job

executor_env = _gpu_check.executor_env
HARDWARE = blender_stop.WORKER_HARDWARE_CHANGED


def _stopped_job():
    job = dict(_gpu_check._gpu_job(), render_device='CPU')
    blender_stop.register_card_stop(job)
    assert blender_stop.request_card_stop(job['id']) is True
    return job


class TestStopPoints:

    def test_before_the_download(self, mocker, executor_env):
        asset = mocker.patch('sethlans_worker_agent.asset_manager.ensure_asset_is_available')
        shutdown = threading.Event()
        job = _stopped_job()

        result = blender_executor.execute_blender_job(job, shutdown_event=shutdown)

        asset.assert_not_called()
        executor_env['popen'].assert_not_called()
        assert result[5] == blender_stop.STOPPED_MESSAGE and job[blender_stop.STOPPED_KEY] == HARDWARE
        assert shutdown.is_set() is False

    def test_before_the_render(self, mocker, executor_env, tmp_path):
        job = dict(_gpu_check._gpu_job(), render_device='CPU')
        blender_stop.register_card_stop(job)

        def lookup(_version):
            blender_stop.request_card_stop(job['id'])  # the card goes while the installed Blender is looked up
            return str(tmp_path / 'blender')
        mocker.patch('sethlans_worker_agent.tool_manager.tool_manager_instance.get_blender_executable_path',
                     side_effect=lookup)

        result = blender_executor.execute_blender_job(job, shutdown_event=threading.Event())

        executor_env['popen'].assert_not_called()
        executor_env['acquire'].assert_not_called()
        assert result[5] == blender_stop.STOPPED_MESSAGE and job[blender_stop.STOPPED_KEY] == HARDWARE

    def test_after_a_render_that_ended_by_itself(self, tmp_path):
        job = _stopped_job()
        stop = blender_stop.job_stop(threading.Event(), job)

        blender_stop.mark_if_cut_short(stop, job, 1, str(tmp_path / 'frame_####.png'))

        assert job[blender_stop.STOPPED_KEY] == HARDWARE

    def test_yield_abort_check(self, mocker):
        status = mocker.patch('sethlans_worker_agent.api_handler.get_job_status', return_value='RENDERING')
        job = blender_job(5)
        blender_stop.register_card_stop(job)
        stop = blender_stop.job_stop(threading.Event(), job)

        assert blender_yield.should_abort(None, 5, stop) is False
        blender_stop.request_card_stop(5)
        assert blender_yield.should_abort(None, 5, stop) is True
        assert status.call_count == 1  # the second answer needs no manager poll

    def test_thumbnail_pass_is_skipped(self, mocker, tmp_path):
        popen = mocker.patch('subprocess.Popen')
        result = blender_thumbnail.render_exr_hdr_thumbnail(
            _stopped_job(), 'blender', str(tmp_path / 'scene.blend'), 1, str(tmp_path / 'render_####.exr'),
            None, stop_event=threading.Event())
        assert result is None
        popen.assert_not_called()

    def test_thumbnail_pass_is_stopped_while_it_runs(self, mocker, tmp_path):
        mocker.patch('sethlans_worker_agent.config.WORKER_TEMP_DIR', str(tmp_path / 'temp'))
        job = dict(_gpu_check._gpu_job(), render_device='CPU')
        blender_stop.register_card_stop(job)
        proc = _gpu_check._fake_process(mocker, None, [])

        def wait(timeout=None):
            blender_stop.request_card_stop(job['id'])  # the card goes while the thumbnail renders
            raise subprocess.TimeoutExpired('blender', timeout)
        proc.wait.side_effect = wait
        mocker.patch('subprocess.Popen', return_value=proc)
        terminate = mocker.patch('sethlans_worker_agent.blender_stop.terminate_process_tree')
        (tmp_path / 'out').mkdir()

        result = blender_thumbnail.render_exr_hdr_thumbnail(
            job, 'blender', str(tmp_path / 'scene.blend'), 1, str(tmp_path / 'out' / 'render_####.exr'),
            None, stop_event=threading.Event())

        assert result is None
        terminate.assert_called_once_with(proc.pid, 238)


class TestRegistry:

    @pytest.mark.parametrize('job_id', [None, 'x', True, -1, -238, 999])
    def test_nothing_for_unknown_retained_or_odd_ids(self, job_id):
        blender_stop.register_card_stop(blender_job(238))
        assert blender_stop.request_card_stop(job_id) is False

    def test_a_later_claim_of_the_same_id_keeps_its_entry(self):
        first, second = blender_job(7), blender_job(7)
        blender_stop.register_card_stop(first)
        blender_stop.register_card_stop(second)

        blender_stop.forget_card_stop(first)
        assert registered_ids() == [7]
        assert blender_stop.request_card_stop(7) is True
        assert second[blender_stop.CARD_STOP_KEY].is_set() and not first[blender_stop.CARD_STOP_KEY].is_set()

    def test_release_after_job_removes_the_entry(self):
        job = blender_job(8)
        blender_stop.register_card_stop(job)
        job_resources.release_after_job(job)
        assert registered_ids() == []

    def test_dispatch_failure_removes_the_entry(self):
        job = blender_job(9)
        blender_stop.register_card_stop(job)
        job_resources.release_after_dispatch_failure(job, None)
        assert registered_ids() == []

    @pytest.mark.parametrize('job, registered', [(blender_job(31), [31]), (blender_job(32, renderer='BLENDER'), [32]),
                                                 (unreal_job(33), [33])], ids=['no-renderer', 'blender', 'unreal'])
    def test_claim_registers_every_job(self, job, registered):
        """Unreal jobs get their own stop too (card selection C-24 m)."""
        selection = type('Selection', (), {'renderer_reservation': None, 'capacity_mode': 'shared'})()
        slot = type('Slot', (), {'card_keys': (), 'device_used': 'GPU'})()
        job_claim._record_claim(job, selection, slot)
        assert registered_ids() == registered
        with job_processor._active_jobs_lock:
            job_processor._active_jobs.clear()
            job_processor._unfinished_job_ids.clear()
