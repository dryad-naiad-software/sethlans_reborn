# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Refused claims do not stall the oldest-first walk (FIFO head-of-line,
chunk 1 concurrency review): a job whose claim the manager refused is
skipped for ``CLAIM_REFUSED_TTL_SECONDS``, then eligible again.
"""
import types

import pytest

from sethlans_worker_agent import job_processor, job_selection

from ._fakes import blender_job, install_capacity


@pytest.fixture()
def clock(monkeypatch):
    now = types.SimpleNamespace(value=1000.0)
    monkeypatch.setattr(job_selection, '_clock', lambda: now.value)
    return now


def _poll(mocker, jobs):
    mocker.patch('sethlans_worker_agent.job_processor._build_poll_params', return_value={})
    mocker.patch('sethlans_worker_agent.api_handler.poll_for_available_jobs', return_value=jobs)


def _claim_ids(mocker, refuse=()):
    attempts = []

    def claim(job_id, worker_id):
        attempts.append(job_id)
        return job_id not in refuse

    mocker.patch('sethlans_worker_agent.api_handler.claim_job', side_effect=claim)
    return attempts


def test_refused_job_is_skipped_and_the_next_one_claimed(mocker, clock, caplog):
    caplog.set_level('INFO', logger='sethlans_worker_agent.job_selection')
    cap = install_capacity(gpu_slot_count=2, cpu_slot_count=0)
    _poll(mocker, [blender_job(1)])
    attempts = _claim_ids(mocker, refuse={1})
    assert job_processor.poll_and_claim_job(worker_id=7) is None
    assert cap.has_no_reservations()

    _poll(mocker, [blender_job(1), blender_job(2)])
    claimed = job_processor.poll_and_claim_job(worker_id=7)
    assert claimed['id'] == 2
    assert attempts == [1, 2]
    clock.value += 10
    _poll(mocker, [blender_job(1), blender_job(3)])
    assert job_processor.poll_and_claim_job(worker_id=7)['id'] == 3
    assert attempts == [1, 2, 3]
    assert caplog.text.count('the manager refused its claim') == 1  # once per TTL window


def test_refused_job_is_eligible_again_after_the_ttl(mocker, clock):
    install_capacity(gpu_slot_count=1, cpu_slot_count=0)
    _poll(mocker, [blender_job(1)])
    attempts = _claim_ids(mocker, refuse={1})
    job_processor.poll_and_claim_job(worker_id=7)
    clock.value += job_selection.CLAIM_REFUSED_TTL_SECONDS - 1
    assert job_processor.poll_and_claim_job(worker_id=7) is None
    assert attempts == [1]
    clock.value += 1
    mocker.patch('sethlans_worker_agent.api_handler.claim_job', return_value=True)
    assert job_processor.poll_and_claim_job(worker_id=7)['id'] == 1


def test_successful_claim_is_not_recorded(mocker, clock):
    install_capacity(gpu_slot_count=1, cpu_slot_count=0)
    _poll(mocker, [blender_job(1)])
    _claim_ids(mocker)
    assert job_processor.poll_and_claim_job(worker_id=7)['id'] == 1
    assert job_selection._refused_claims == {}


def test_no_slot_or_shutdown_is_not_recorded(mocker, clock):
    install_capacity(gpu_slot_count=0, cpu_slot_count=1)
    _poll(mocker, [blender_job(1, device='GPU')])
    _claim_ids(mocker)
    assert job_processor.poll_and_claim_job(worker_id=7) is None
    assert job_selection._refused_claims == {}


@pytest.mark.parametrize('failure', ['network', 'server_error', 'auth'])
def test_any_claim_failure_starts_the_skip(mocker, clock, failure):
    """F5: a network error or any non-200 status is a refusal too (real api_handler)."""
    import requests
    install_capacity(gpu_slot_count=1, cpu_slot_count=0)
    _poll(mocker, [blender_job(1)])
    if failure == 'network':
        mocker.patch('sethlans_worker_agent.api_handler._retry_request',
                     side_effect=requests.exceptions.ConnectionError('down'))
    else:
        response = mocker.Mock(status_code=500 if failure == 'server_error' else 401, text='')
        mocker.patch('sethlans_worker_agent.api_handler._retry_request', return_value=response)
        mocker.patch('sethlans_worker_agent.api_handler.handle_auth_response',
                     return_value=failure == 'auth')
    assert job_processor.poll_and_claim_job(worker_id=7) is None
    assert 1 in job_selection._refused_claims
    assert job_selection.CLAIM_REFUSED_TTL_SECONDS == 60.0


def test_records_are_pruned_and_capped(clock):
    for job_id in range(job_selection.SKIP_RECORD_MAX_ENTRIES + 5):
        job_selection.record_claim_refused(job_id)
    assert len(job_selection._refused_claims) == job_selection.SKIP_RECORD_MAX_ENTRIES
    clock.value += job_selection.CLAIM_REFUSED_TTL_SECONDS
    job_selection.record_claim_refused('new')
    assert list(job_selection._refused_claims) == ['new']


def test_pending_probe_wait_applies_to_the_first_unguarded_job(clock, fake_unreal):
    """F8: a refused first job does not stop the next job's one-cycle probe wait."""
    from ._fakes import unreal_job
    install_capacity()
    job_selection.record_claim_refused(1)
    fake_unreal.pending_ids.add(2)
    jobs = [blender_job(1), unreal_job(2), blender_job(3)]
    assert job_selection.select_claimable_job(jobs, job_processor.get_worker_capacity()) is None
    selection = job_selection.select_claimable_job(jobs, job_processor.get_worker_capacity())
    assert selection.job['id'] == 3  # the wait happens once per job id
    selection.renderer_reservation.release()


def test_unfinished_first_job_does_not_stop_the_probe_wait(clock, fake_unreal):
    from ._fakes import unreal_job
    install_capacity()
    with job_processor._active_jobs_lock:
        job_processor._unfinished_job_ids.add(1)
    fake_unreal.pending_ids.add(2)
    jobs = [blender_job(1), unreal_job(2), blender_job(3)]
    assert job_selection.select_claimable_job(jobs, job_processor.get_worker_capacity()) is None
