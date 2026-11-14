# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Executor dispatch through the real claim path and job thread
(Unreal foundation FR-T-7 ``test_executor_dispatch``; FR-WX-4, FR-WX-7).

A fake Unreal executor (the real one lands in spec chunk 7) is found by
the real lazy import in ``executors.get_executor``. Each case polls,
selects, reserves the whole machine, claims, starts the job thread via
``get_and_claim_job`` and joins it, then checks the manager calls and
that the token and the exclusive slots are released (or, when the
reservation is retained, that both stay held).
"""
import pytest

from sethlans_worker_agent import api_handler, blender_executor, job_processor

from tests.unit.worker.executors._fakes import (
    install_fake_unreal,
    make_result,
    reset_chunk3_state,
    unreal_job,
)

from ._capacity_harness import install_fake_capacity, stub_scan_for_local_blenders


@pytest.fixture(autouse=True)
def _chunk3_state():
    reset_chunk3_state()
    yield
    reset_chunk3_state()


@pytest.fixture()
def worker(mocker, monkeypatch):
    install_fake_capacity(mocker, gpu_count=2, cpu_cores=4)
    stub_scan_for_local_blenders(mocker)
    fake = install_fake_unreal(monkeypatch)
    mocks = {
        'poll': mocker.patch.object(api_handler, 'poll_for_available_jobs'),
        'claim': mocker.patch.object(api_handler, 'claim_job', return_value=True),
        'update': mocker.patch.object(api_handler, 'update_job_status'),
        'upload': mocker.patch.object(api_handler, 'upload_render_output', return_value=True),
        'blender': mocker.patch.object(blender_executor, 'execute_blender_job'),
    }
    return fake, mocks


def _dispatch(mocks, job):
    mocks['poll'].return_value = [job]
    thread = job_processor.get_and_claim_job(worker_id=1)
    assert thread is not None
    thread.join(timeout=10)
    assert not thread.is_alive()


@pytest.mark.parametrize('success,canceled,status', [
    (True, False, 'DONE'), (False, False, 'ERROR'), (False, True, 'CANCELED'),
])
def test_status_payloads_and_release(worker, success, canceled, status):
    fake, mocks = worker
    fake.result = make_result(success=success, was_canceled=canceled,
                              error_message='' if success else 'ue failed')
    _dispatch(mocks, unreal_job(31))

    payload = mocks['update'].call_args.args[1]
    assert payload['status'] == status
    assert payload['render_time_seconds'] == 7
    mocks['blender'].assert_not_called()
    capacity = job_processor.get_worker_capacity()
    assert capacity.has_no_reservations()
    assert fake.token_free()
    assert job_processor.get_active_jobs_snapshot() == {}
    assert job_processor.get_unfinished_job_ids() == []


def test_released_result_sends_no_patch(worker):
    fake, mocks = worker
    fake.result = make_result(released=True, output_path='/never/uploaded.png')
    _dispatch(mocks, unreal_job(32))

    mocks['update'].assert_not_called()
    mocks['upload'].assert_not_called()
    assert job_processor.get_worker_capacity().has_no_reservations()
    assert fake.token_free()


def test_exclusive_hold_blocks_polling_until_release(worker):
    fake, mocks = worker
    capacity = job_processor.get_worker_capacity()
    observed = {}

    def execute(job_data, ctx):
        observed['prefs'] = capacity.free_device_prefs()
        observed['assignments'] = capacity.gpu_assignments_snapshot()
        return make_result()

    fake.execute = execute
    _dispatch(mocks, unreal_job(33))
    assert observed['prefs'] == []
    assert observed['assignments'] == {0: 33, 1: 33}
    assert capacity.has_no_reservations()


def test_retained_reservation_keeps_token_and_slots(worker):
    fake, mocks = worker
    fake.retain = True
    fake.result = make_result(success=False, error_message='survivors')
    _dispatch(mocks, unreal_job(34))

    capacity = job_processor.get_worker_capacity()
    assert not fake.token_free()
    assert capacity.is_full() and capacity.free_device_prefs() == []
    assert job_processor.get_active_jobs_snapshot() == {}
    mocks['poll'].reset_mock()
    assert job_processor.get_and_claim_job(worker_id=1) is None
    mocks['poll'].assert_not_called()
