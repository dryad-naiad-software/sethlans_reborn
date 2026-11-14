# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Claim path leak checks (Unreal foundation FR-WX-7, FR-WX-8, FR-RT-17;
FR-T-5 "Leak paths", "Shutdown race"; AC-20).

After every non-success exit the capacity is fully free, ``_active_jobs``
and ``_unfinished_job_ids`` are empty, and a second Unreal
``try_reserve`` succeeds (the token was returned).
"""
import sys
import threading
import types

import pytest

import sethlans_worker_agent
from sethlans_worker_agent import agent, job_processor

from ._fakes import blender_job, install_capacity, unreal_job


def _poll_returns(mocker, jobs):
    mocker.patch('sethlans_worker_agent.job_processor._build_poll_params', return_value={})
    mocker.patch('sethlans_worker_agent.api_handler.poll_for_available_jobs', return_value=jobs)


def _assert_everything_free(cap, fake_unreal):
    assert cap.has_no_reservations()
    assert cap.free_device_prefs() == ['GPU', 'CPU', 'ANY']
    assert job_processor.get_active_jobs_snapshot() == {}
    assert job_processor.get_unfinished_job_ids() == []
    second = fake_unreal.try_reserve(unreal_job(999))
    assert second is not None
    second.release()


class TestLeakPaths:

    def test_reserve_exclusive_none_releases_token(self, mocker, fake_unreal):
        cap = install_capacity()
        _poll_returns(mocker, [unreal_job(1)])
        mocker.patch.object(cap, 'reserve_exclusive', return_value=None)
        claim = mocker.patch('sethlans_worker_agent.api_handler.claim_job')
        assert job_processor.poll_and_claim_job(worker_id=1) is None
        claim.assert_not_called()
        _assert_everything_free(cap, fake_unreal)

    def test_reserve_for_job_none_releases_reservation(self, mocker, fake_unreal):
        cap = install_capacity(gpu_slot_count=0, cpu_slot_count=1)
        _poll_returns(mocker, [blender_job(1, device='GPU')])
        claim = mocker.patch('sethlans_worker_agent.api_handler.claim_job')
        assert job_processor.poll_and_claim_job(worker_id=1) is None
        claim.assert_not_called()
        assert cap.has_no_reservations()

    def test_claim_false_releases_both(self, mocker, fake_unreal):
        cap = install_capacity()
        _poll_returns(mocker, [unreal_job(1)])
        mocker.patch('sethlans_worker_agent.api_handler.claim_job', return_value=False)
        assert job_processor.poll_and_claim_job(worker_id=1) is None
        _assert_everything_free(cap, fake_unreal)

    @pytest.mark.parametrize('exc', [RuntimeError('net'), SystemExit(1)])
    def test_claim_exception_releases_both(self, mocker, fake_unreal, exc):
        cap = install_capacity()
        _poll_returns(mocker, [unreal_job(1)])
        mocker.patch('sethlans_worker_agent.api_handler.claim_job', side_effect=exc)
        with pytest.raises(type(exc)):
            job_processor.poll_and_claim_job(worker_id=1)
        _assert_everything_free(cap, fake_unreal)

    def test_release_order_token_then_slot(self, mocker, fake_unreal):
        cap = install_capacity()
        _poll_returns(mocker, [unreal_job(1)])
        mocker.patch('sethlans_worker_agent.api_handler.claim_job', return_value=False)
        real_release = cap.release_slot
        mocker.patch.object(cap, 'release_slot', side_effect=lambda jid: (
            fake_unreal.events.append('slot_released'), real_release(jid)))
        job_processor.poll_and_claim_job(worker_id=1)
        assert fake_unreal.events == ['token_released', 'slot_released']


class TestShutdownRace:

    def test_no_claim_while_shutting_down(self, mocker, fake_unreal):
        cap = install_capacity()
        _poll_returns(mocker, [unreal_job(1)])
        claim = mocker.patch('sethlans_worker_agent.api_handler.claim_job')
        stopping = threading.Event()
        real_reserve = cap.reserve_exclusive

        def reserve_then_sigterm(job_id):
            slot = real_reserve(job_id)
            stopping.set()
            return slot

        mocker.patch.object(cap, 'reserve_exclusive', side_effect=reserve_then_sigterm)
        mocker.patch.object(agent, '_shutdown_event', stopping)
        assert job_processor.poll_and_claim_job(worker_id=1) is None
        claim.assert_not_called()
        _assert_everything_free(cap, fake_unreal)


class TestSuccess:

    def test_unreal_claim_records_reservations(self, mocker, fake_unreal):
        cap = install_capacity(gpu_slot_count=2, cpu_slot_count=1)
        _poll_returns(mocker, [unreal_job(7)])
        mocker.patch('sethlans_worker_agent.api_handler.claim_job', return_value=True)
        job = job_processor.poll_and_claim_job(worker_id=1)
        assert job['_reservation'].gpu_indices == []
        assert job['assigned_gpu_index'] is None
        assert job['_renderer_reservation'] is not None
        assert cap.free_device_prefs() == []
        entry = job_processor.get_active_jobs_snapshot()[7]
        assert entry['renderer'] == 'UNREAL' and entry['exclusive'] is True
        assert job_processor.get_unfinished_job_ids() == [7]

    def test_blender_claim_entry_shape(self, mocker):
        install_capacity()
        _poll_returns(mocker, [blender_job(3)])
        mocker.patch('sethlans_worker_agent.api_handler.claim_job', return_value=True)
        job = job_processor.poll_and_claim_job(worker_id=1)
        assert job['assigned_gpu_index'] == 0
        entry = job_processor.get_active_jobs_snapshot()[3]
        assert entry['renderer'] == 'BLENDER' and entry['exclusive'] is False
        assert entry['device_used'] == 'GPU' and entry['gpu_index'] == 0
        assert job_processor.get_unfinished_job_ids() == [3]

    def test_record_failure_releases_and_logs_critical(self, mocker, fake_unreal, caplog):
        cap = install_capacity()
        _poll_returns(mocker, [unreal_job(8)])
        mocker.patch('sethlans_worker_agent.api_handler.claim_job', return_value=True)
        mocker.patch('sethlans_worker_agent.job_claim._record_claim', side_effect=RuntimeError('x'))
        with pytest.raises(RuntimeError):
            job_processor.poll_and_claim_job(worker_id=1)
        assert any(r.levelname == 'CRITICAL' for r in caplog.records)
        _assert_everything_free(cap, fake_unreal)


class TestThreadStartFailure:

    def _start_fails(self, mocker):
        class _NoStartThread(threading.Thread):
            def start(self):
                raise RuntimeError("can't start new thread")

        fake_threading = types.SimpleNamespace(Thread=_NoStartThread)
        mocker.patch.object(job_processor, 'threading', fake_threading)

    def test_unreal_releases_everything_and_hands_job_back(self, mocker, monkeypatch, fake_unreal):
        cap = install_capacity()
        _poll_returns(mocker, [unreal_job(4)])
        mocker.patch('sethlans_worker_agent.api_handler.claim_job', return_value=True)
        fake_extras = types.ModuleType('sethlans_worker_agent.api_extras')
        fake_extras.release_job = mocker.Mock()
        monkeypatch.setitem(sys.modules, 'sethlans_worker_agent.api_extras', fake_extras)
        monkeypatch.setattr(sethlans_worker_agent, 'api_extras', fake_extras, raising=False)  # chunk 7b: real module exists
        self._start_fails(mocker)
        assert job_processor.get_and_claim_job(worker_id=1) is None
        fake_extras.release_job.assert_called_once_with(4, 'dispatch_failed')
        _assert_everything_free(cap, fake_unreal)

    def test_blender_releases_everything_without_release_call(self, mocker, monkeypatch, fake_unreal):
        cap = install_capacity()
        _poll_returns(mocker, [blender_job(5)])
        mocker.patch('sethlans_worker_agent.api_handler.claim_job', return_value=True)
        fake_extras = types.ModuleType('sethlans_worker_agent.api_extras')
        fake_extras.release_job = mocker.Mock()
        monkeypatch.setitem(sys.modules, 'sethlans_worker_agent.api_extras', fake_extras)
        monkeypatch.setattr(sethlans_worker_agent, 'api_extras', fake_extras, raising=False)  # chunk 7b: real module exists
        self._start_fails(mocker)
        assert job_processor.get_and_claim_job(worker_id=1) is None
        fake_extras.release_job.assert_not_called()
        _assert_everything_free(cap, fake_unreal)

    def test_release_failure_is_logged_not_raised(self, mocker, fake_unreal):
        cap = install_capacity()
        _poll_returns(mocker, [unreal_job(6)])
        mocker.patch('sethlans_worker_agent.api_handler.claim_job', return_value=True)
        mocker.patch('sethlans_worker_agent.api_extras.release_job', side_effect=RuntimeError('boom'))
        self._start_fails(mocker)
        assert job_processor.get_and_claim_job(worker_id=1) is None
        _assert_everything_free(cap, fake_unreal)
