# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Job lifecycle dispatch and release (Unreal foundation FR-WX-4, FR-WX-5,
FR-RT-17; FR-T-5 "Leak paths", "Release order", retained reservations,
"Heartbeat payload" ``_unfinished_job_ids``).
"""
import pytest

from sethlans_worker_agent import job_lifecycle, job_processor
from sethlans_worker_agent.executors import UnsupportedRendererError
from sethlans_worker_agent.job_lifecycle import process_claimed_job

from ._fakes import blender_job, install_capacity, make_result, unreal_job


@pytest.fixture()
def api(mocker):
    mocker.patch('sethlans_worker_agent.job_processor._build_poll_params', return_value={})
    mocker.patch('sethlans_worker_agent.api_handler.claim_job', return_value=True)
    return {
        'poll': mocker.patch('sethlans_worker_agent.api_handler.poll_for_available_jobs'),
        'update': mocker.patch('sethlans_worker_agent.api_handler.update_job_status'),
        'upload': mocker.patch(
            'sethlans_worker_agent.api_handler.upload_render_output', return_value=True),
    }


def _claim(api, job):
    api['poll'].return_value = [job]
    claimed = job_processor.poll_and_claim_job(worker_id=1)
    assert claimed is job
    return claimed


def _statuses(api):
    return [call.args[1].get('status') for call in api['update'].call_args_list]


class TestUnreal:

    def test_done_uses_executor_render_time(self, api, fake_unreal):
        cap = install_capacity()
        process_claimed_job(_claim(api, unreal_job(1)))
        payload = api['update'].call_args.args[1]
        assert payload['status'] == 'DONE'
        assert payload['render_time_seconds'] == 7
        assert cap.has_no_reservations() and fake_unreal.token_free()

    def test_inner_finally_releases_token_before_slot(self, api, fake_unreal, mocker):
        cap = install_capacity()
        job = _claim(api, unreal_job(2))
        real_release = cap.release_slot
        mocker.patch.object(cap, 'release_slot', side_effect=lambda jid: (
            fake_unreal.events.append('slot_released'), real_release(jid)))
        process_claimed_job(job)
        assert fake_unreal.events[:3] == ['execute', 'token_released', 'slot_released']

    def test_released_result_skips_report_and_upload(self, api, fake_unreal):
        cap = install_capacity()
        fake_unreal.result = make_result(released=True, output_path='/x.png')
        process_claimed_job(_claim(api, unreal_job(3)))
        api['update'].assert_not_called()
        api['upload'].assert_not_called()
        assert cap.has_no_reservations() and fake_unreal.token_free()
        assert job_processor.get_unfinished_job_ids() == []

    def test_retained_reservation_keeps_token_and_slots(self, api, fake_unreal):
        cap = install_capacity(gpu_slot_count=2, cpu_slot_count=1)
        fake_unreal.retain = True
        fake_unreal.result = make_result(success=False, error_message='survivors')
        process_claimed_job(_claim(api, unreal_job(4)))
        assert not fake_unreal.token_free()
        assert cap.free_device_prefs() == [] and cap.is_full()
        assert job_processor.get_active_jobs_snapshot() == {}
        assert job_processor.get_unfinished_job_ids() == []
        # A following poll claims nothing: the gate never even polls.
        api['poll'].reset_mock()
        assert job_processor.poll_and_claim_job(worker_id=1) is None
        api['poll'].assert_not_called()

    def test_get_executor_raising_in_thread_releases_all(self, api, fake_unreal, mocker):
        cap = install_capacity()
        job = _claim(api, unreal_job(5))
        mocker.patch('sethlans_worker_agent.executors.get_executor',
                     side_effect=UnsupportedRendererError('UNREAL'))
        process_claimed_job(job)
        api['update'].assert_called_once_with(5, {
            'status': 'ERROR',
            'error_message': "Unsupported renderer 'UNREAL' on this worker.",
        })
        assert cap.has_no_reservations()
        assert job_processor.get_active_jobs_snapshot() == {}
        assert job_processor.get_unfinished_job_ids() == []
        second = fake_unreal.try_reserve(unreal_job(6))
        assert second is not None
        second.release()

    @pytest.mark.parametrize('success,canceled,status', [
        (True, False, 'DONE'), (False, False, 'ERROR'), (False, True, 'CANCELED'),
    ])
    def test_status_payloads(self, api, fake_unreal, success, canceled, status):
        install_capacity()
        fake_unreal.result = make_result(success=success, was_canceled=canceled)
        process_claimed_job(_claim(api, unreal_job(8)))
        assert _statuses(api) == [status]


class TestUnfinishedJobIds:

    def test_kept_through_upload_and_report(self, api, mocker, tmp_path):
        cap = install_capacity()
        out = tmp_path / 'f.png'
        out.write_bytes(b'PNG')
        seen = []
        mocker.patch(
            'sethlans_worker_agent.blender_executor.execute_blender_job',
            return_value=(True, False, False, 'Time: 00:01.00', '', '', str(out), None, None, None),
        )

        def upload(*args, **kwargs):
            # Blender's slot is freed before the upload, as today.
            seen.append(('upload', job_processor.get_unfinished_job_ids(), cap.has_no_reservations()))
            return True

        api['upload'].side_effect = upload
        api['update'].side_effect = lambda *a, **k: seen.append(
            ('update', job_processor.get_unfinished_job_ids(), cap.has_no_reservations()))
        process_claimed_job(_claim(api, blender_job(9)))
        assert seen == [('upload', [9], True), ('update', [9], True)]
        assert job_processor.get_unfinished_job_ids() == []

    def test_exception_before_inner_try_hits_safety_net(self, api, fake_unreal, mocker):
        cap = install_capacity()
        job = _claim(api, unreal_job(10))
        mocker.patch.object(job_lifecycle, '_make_yield_monitor_factory',
                            side_effect=RuntimeError('early'))
        process_claimed_job(job)
        assert _statuses(api) == ['ERROR']
        assert cap.has_no_reservations() and fake_unreal.token_free()
        assert job_processor.get_active_jobs_snapshot() == {}
        assert job_processor.get_unfinished_job_ids() == []


class TestSameJobIdReclaimed:
    """Review M1: a release must never free a later claim of the same id."""

    def test_unfinished_id_skipped_until_previous_run_exits(self, api, mocker, tmp_path):
        cap = install_capacity(gpu_slot_count=2, cpu_slot_count=0)
        out = tmp_path / 'f.png'
        out.write_bytes(b'PNG')
        mocker.patch(
            'sethlans_worker_agent.blender_executor.execute_blender_job',
            return_value=(True, False, False, '', '', '', str(out), None, None, None),
        )
        outcomes = []

        def upload(*args, **kwargs):
            # Run A is past its inner finally: re-poll with X at the head.
            api['poll'].return_value = [blender_job(42), blender_job(43)]
            outcomes.append(job_processor.poll_and_claim_job(worker_id=1))
            return True

        api['upload'].side_effect = upload
        process_claimed_job(_claim(api, blender_job(42)))
        assert outcomes[0]['id'] == 43  # 42 skipped while A unfinished
        assert job_processor.get_unfinished_job_ids() == [43]
        cap.release_slot(43)
        api['poll'].return_value = [blender_job(42)]
        assert job_processor.poll_and_claim_job(worker_id=1)['id'] == 42

    def test_safety_net_leaves_reclaimed_slot_held(self, api, mocker, tmp_path):
        cap = install_capacity()
        out = tmp_path / 'f.png'
        out.write_bytes(b'PNG')
        mocker.patch(
            'sethlans_worker_agent.blender_executor.execute_blender_job',
            return_value=(True, False, False, '', '', '', str(out), None, None, None),
        )

        def upload(*args, **kwargs):
            assert cap.reserve_for_job(42, 'GPU') is not None
            return True

        api['upload'].side_effect = upload
        process_claimed_job(_claim(api, blender_job(42)))
        assert cap.gpu_assignments_snapshot() == {0: 42}

    def test_forget_keeps_a_newer_active_entry(self, api, mocker):
        install_capacity()
        mocker.patch(
            'sethlans_worker_agent.blender_executor.execute_blender_job',
            return_value=(True, False, False, '', '', '', None, None, None, None),
        )
        newer = {'job_id': 42, 'name': 'B'}

        def update(*args, **kwargs):
            with job_processor._active_jobs_lock:
                job_processor._active_jobs[42] = newer

        api['update'].side_effect = update
        process_claimed_job(_claim(api, blender_job(42)))
        assert job_processor.get_active_jobs_snapshot() == {42: newer}


class TestBlenderParity:

    @pytest.mark.parametrize('extra', [{}, {'renderer': None}, {'renderer': 'BLENDER'}])
    def test_null_or_missing_renderer_renders_as_blender(self, api, mocker, extra):
        cap = install_capacity()
        spy = mocker.patch(
            'sethlans_worker_agent.blender_executor.execute_blender_job',
            return_value=(True, False, False, 'Time: 00:02.00 (Saving: 00:00.10)', '', '', None, None, None, None),
        )
        process_claimed_job(_claim(api, blender_job(11, **extra)))
        spy.assert_called_once()
        assert spy.call_args.kwargs['assigned_gpu_index'] == 0
        payload = api['update'].call_args.args[1]
        assert payload['status'] == 'DONE' and payload['render_time_seconds'] == 2
        assert cap.has_no_reservations()

    def test_unknown_renderer_in_thread_reports_message(self, api):
        install_capacity()
        process_claimed_job({'id': 12, 'name': 'odd', 'renderer': 'MAYA'})
        api['update'].assert_called_once_with(12, {
            'status': 'ERROR',
            'error_message': "Unsupported renderer 'MAYA' on this worker.",
        })
