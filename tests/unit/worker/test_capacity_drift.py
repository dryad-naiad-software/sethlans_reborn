# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the periodic card check and the uncached GPU detection (#239, FR-CK-1..3, FR-CL-6).

A hardware change no longer ends the worker (FR-CL-6): a ticked card that
is gone raises one CRITICAL line and one alert, stops exactly the jobs on
that card through their own stop events, leaves the shutdown event alone and
requests a slot rebuild. Nothing changes the exit code any more; the
capacity package has no exit-code flag. A failed probe or detection changes
nothing (AC-11).
"""
import json
import logging
import subprocess

import pytest

from sethlans_worker_agent import agent_shutdown, blender_stop, hardware_detection
from sethlans_worker_agent import capacity as capacity_module
from sethlans_worker_agent.capacity import WorkerCapacity, drift as drift_module, rebuild
from sethlans_worker_agent.cards import store
from tests.unit.worker._card_check_env import CardEnv
from tests.unit.worker.executors._fakes import blender_job, key, profile

CHECK = 'sethlans_worker_agent.capacity.card_check'


@pytest.fixture
def env(monkeypatch):
    """Two ticked cards, both in use; jobs 11 (card 0) and 12 (card 1) hold them, each with its own stop."""
    machine = CardEnv(monkeypatch, count=2)
    cap = WorkerCapacity(profile(gpu_slot_count=2))
    jobs = {}
    for job_id in (11, 12):
        cap.reserve_for_job(job_id, 'GPU')
        jobs[job_id] = blender_job(job_id)
        blender_stop.register_card_stop(jobs[job_id])
    machine.cap, machine.jobs = cap, jobs
    return machine


def _stopped(env):
    return sorted(job_id for job_id, job in env.jobs.items() if job[blender_stop.CARD_STOP_KEY].is_set())


def _critical(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]


class TestPeriodicCheck:

    def test_same_cards_change_nothing(self, env, caplog):
        assert env.cap.assert_gpu_list_unchanged() is None

        assert env.detections == 1 and env.probes == 1
        assert env.alerts() == [] and _stopped(env) == [] and _critical(caplog) == []
        assert rebuild.pending() is False
        assert agent_shutdown._shutdown_event.is_set() is False

    def test_lost_card_alerts_once_stops_its_job_only_and_requests_a_rebuild(self, env, caplog):
        """AC-8 (unit): one CRITICAL line, one card_lost alert, the stop for card 1's job, no shutdown."""
        env.lose(1)

        with caplog.at_level(logging.CRITICAL, logger=CHECK):
            assert env.cap.assert_gpu_list_unchanged() is None

        assert env.alert_keys() == [('card_lost', key(1))]
        [line] = _critical(caplog)
        assert line == f"Graphics card 'GPU 1' ({key(1)}) is lost ('missing from the card list')."
        assert _stopped(env) == [12]
        assert env.jobs[12][blender_stop.CARD_STOP_KEY].is_set()
        assert agent_shutdown._shutdown_event.is_set() is False
        assert rebuild.pending() is True
        assert env.cap.gpu_assignments_snapshot() == {key(0): 11, key(1): 12}  # released by the job, not here

    def test_level_triggered_without_a_second_alert(self, env, caplog):
        env.lose(1)
        env.cap.assert_gpu_list_unchanged()
        caplog.clear()

        with caplog.at_level(logging.CRITICAL, logger=CHECK):
            env.cap.assert_gpu_list_unchanged()

        assert env.alert_keys() == [('card_lost', key(1))]
        assert _critical(caplog) == []  # the alert is open: no new line
        assert _stopped(env) == [12]

    def test_blender_no_longer_offering_a_card_holds_it_from_blender(self, env):
        env.fresh = env.blender_records({key(0)})  # the OS still lists card 1

        env.cap.assert_gpu_list_unchanged()

        assert env.alert_keys() == [('card_lost_blender', key(1))]
        assert _stopped(env) == [12]
        assert rebuild.pending() is True

    def test_changed_model_is_alerted_and_stopped(self, env):
        env.compute['cards'][0]['device_id'] = 0x1234  # saved as another model

        env.cap.assert_gpu_list_unchanged()

        assert env.alert_keys() == [('card_changed', key(0))]
        assert _stopped(env) == [11]

    @pytest.mark.parametrize('failure', ['probe', 'detection', 'read'])
    def test_failed_probe_detection_or_read_changes_nothing(self, env, failure, caplog):
        """AC-11, C-20: try again later; no alert, no stop, no rebuild."""
        env.lose(1)
        env.fail_probe = failure == 'probe'
        env.fresh = None if failure == 'detection' else env.fresh
        env.fail_read = failure == 'read'

        assert env.cap.assert_gpu_list_unchanged() == drift_module.DETECTION_FAILED

        assert env.alerts() == [] and env.writes == [] and _stopped(env) == [] and _critical(caplog) == []
        assert rebuild.pending() is False
        assert env.detections == (1 if failure == 'detection' else 0)

    def test_failed_alert_write_still_stops_and_requests_the_rebuild(self, env, caplog):
        """AC-42 / C5: the alert is reported from memory."""
        env.lose(1)
        env.fail_write = OSError('disk full')

        with caplog.at_level(logging.CRITICAL, logger=CHECK):
            assert env.cap.assert_gpu_list_unchanged() is None

        assert env.alerts() == []
        assert [(a['kind'], a['key']) for a in store.snapshot().alerts] == [('card_lost', key(1))]
        assert len(_critical(caplog)) == 1
        assert _stopped(env) == [12]
        assert rebuild.pending() is True

    def test_method_cpu_without_ticks_probes_nothing(self, monkeypatch):
        machine = CardEnv(monkeypatch, count=2, method='cpu', ticked=())
        assert WorkerCapacity(profile(gpu_slot_count=0)).assert_gpu_list_unchanged() is None
        assert (machine.probes, machine.detections) == (0, 0)
        assert rebuild.pending() is False

    def test_method_cpu_ticked_card_is_checked_without_blender(self, monkeypatch, caplog):
        """A card ticked for Unreal under method cpu: OS probe and alert, no Blender detection, no exit."""
        machine = CardEnv(monkeypatch, count=2, method='cpu', ticked=(1,))
        cap = WorkerCapacity(profile(gpu_slot_count=0))
        assert cap.assert_gpu_list_unchanged() is None
        assert (machine.probes, machine.detections, machine.alerts()) == (1, 0, [])

        machine.lose(1)
        with caplog.at_level(logging.CRITICAL, logger=CHECK):
            assert cap.assert_gpu_list_unchanged() is None

        assert machine.alert_keys() == [('card_lost', key(1))]
        assert len(_critical(caplog)) == 1
        assert machine.detections == 0
        assert rebuild.pending() is False  # no Blender card was in use, none is now
        assert agent_shutdown._shutdown_event.is_set() is False

    def test_names_are_logged_ascii_safe(self, env, caplog):
        env.compute['cards'][1]['name'] = 'GPU 显卡®'
        env.lose(1)

        with caplog.at_level(logging.CRITICAL, logger=CHECK):
            env.cap.assert_gpu_list_unchanged()

        [line] = _critical(caplog)
        line.encode('ascii')
        assert ascii('GPU 显卡®') in line


class TestNoExit:
    """FR-CL-6: the exit-code flag and the drift-only termination are gone."""

    @pytest.mark.parametrize('name', ['get_drift_exit_code', 'set_drift_exit_code', 'reset_drift_exit_code',
                                      '_drift_detected_exit_code'])
    def test_no_exit_code_flag(self, name):
        assert not hasattr(drift_module, name)
        assert not hasattr(capacity_module, name)

    def test_no_drift_termination_paths(self):
        from sethlans_worker_agent import job_processor, job_resources
        assert not hasattr(job_processor, 'terminate_all_active_jobs_for_drift')
        assert not hasattr(job_resources, 'request_shutdown_for_stop')
        assert not hasattr(job_resources, 'request_shutdown_for_release')
        assert not hasattr(blender_stop, 'mark_hardware_change')

    def test_lost_card_never_sets_the_shutdown_event(self, env):
        env.lose(0)
        env.lose(1)
        env.cap.assert_gpu_list_unchanged()
        assert _stopped(env) == [11, 12]
        assert agent_shutdown._shutdown_event.is_set() is False


@pytest.fixture
def fake_blender(mocker, monkeypatch, tmp_path):
    """The Blender that filled the cached list exists; subprocess.run is mocked."""
    exe = tmp_path / 'blender.exe'
    exe.write_text('')
    monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', str(exe))
    return mocker.patch('sethlans_worker_agent.capacity.drift.subprocess.run')


class TestDetectPhysicalGpusNow:

    def test_returns_none_when_no_detection_blender_is_known(self, monkeypatch, mocker):
        """No remembered Blender and none installed is a skip signal, not 'no GPUs'."""
        monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', None)
        mocker.patch('sethlans_worker_agent.capacity.drift._choose_fallback_blender', return_value=None)
        run = mocker.patch('sethlans_worker_agent.capacity.drift.subprocess.run')
        assert drift_module.detect_physical_gpus_now() is None
        run.assert_not_called()

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
