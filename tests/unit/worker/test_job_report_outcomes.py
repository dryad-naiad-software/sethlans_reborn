# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Result reporting outcomes (#249, #246, #247).

DONE only when the output was uploaded: a missing output file or a failed
upload is an ERROR whose message travels in the final status PATCH, and a
failed upload keeps the rendered file. A user-cancelled job whose CANCELED
report is answered 409 (already terminal) is still recorded as cancelled.
A hand-back that reached the auto-requeue limit is logged as such. A slot
retained for an unkillable process cannot be freed by a later claim of the
same job id.
"""
import logging

from sethlans_worker_agent import blender_hand_back, job_processor, job_report
from sethlans_worker_agent.api_conflict import NO_LONGER_ASSIGNED
from sethlans_worker_agent.capacity import CapacityProfile, WorkerCapacity
from sethlans_worker_agent.capacity.unreal_hold import retained_owner
from tests.unit.worker.executors._fakes import make_result
from tests.unit.worker.executors._fakes import fake_cards


def _report(mocker, result, upload=True, update=True):
    up = mocker.patch('sethlans_worker_agent.api_handler.upload_render_output', return_value=upload)
    patch = mocker.patch('sethlans_worker_agent.api_handler.update_job_status', return_value=update)
    job_report.report_result({'id': 4, 'name': 'Frame'}, result, job_processor)
    return up, patch


class TestDoneOnlyAfterUpload:

    def test_missing_output_file_is_reported_error(self, mocker, tmp_path):
        result = make_result(output_path=str(tmp_path / 'frame_0001.png'), render_time_seconds=3)

        up, patch = _report(mocker, result)

        up.assert_not_called()
        payload = patch.call_args.args[1]
        assert payload['status'] == 'ERROR'
        assert payload['error_message'] == 'Blender finished but produced no output file (frame_0001.png).'

    def test_failed_upload_is_reported_error_and_the_file_kept(self, mocker, tmp_path):
        out = tmp_path / 'frame_0001.png'
        out.write_bytes(b'png')

        _up, patch = _report(mocker, make_result(output_path=str(out)), upload=False)

        payload = patch.call_args.args[1]
        assert payload['status'] == 'ERROR'
        assert 'uploading its output failed' in payload['error_message']
        assert 'frame_0001.png' in payload['error_message']
        assert str(tmp_path) not in payload['error_message']  # the local folder is not reported
        assert out.exists()

    def test_uploaded_output_is_done_and_removed(self, mocker, tmp_path):
        out = tmp_path / 'frame_0001.png'
        out.write_bytes(b'png')

        _up, patch = _report(mocker, make_result(output_path=str(out), error_message=''))

        assert patch.call_args.args[1]['status'] == 'DONE'
        assert patch.call_args.args[1]['error_message'] == ''
        assert not out.exists()

    def test_multi_frame_job_without_single_output_keeps_done(self, mocker):
        """No single output path (an animation range in one job): unchanged, DONE."""
        up, patch = _report(mocker, make_result(output_path=None))

        up.assert_not_called()
        assert patch.call_args.args[1]['status'] == 'DONE'


class TestCancelAnswered409:

    def test_cancelled_job_is_still_recorded(self, mocker):
        result = make_result(success=False, was_canceled=True, error_message='Job was canceled by user request.')

        _report(mocker, result, update=NO_LONGER_ASSIGNED)

        recent = job_processor.get_recent_jobs_snapshot()
        assert [entry['status'] for entry in recent] == ['CANCELED']

    def test_other_results_answered_409_are_dropped(self, mocker):
        _report(mocker, make_result(success=False, error_message='boom'), update=NO_LONGER_ASSIGNED)

        assert job_processor.get_recent_jobs_snapshot() == []


class TestHandBackAtTheLimit:

    def test_capped_hand_back_is_logged_as_error(self, mocker, caplog):
        response = mocker.Mock(status_code=200)
        response.json.return_value = {'status': 'error'}
        mocker.patch('sethlans_worker_agent.api_handler._retry_request', return_value=response)
        mocker.patch('sethlans_worker_agent.api_handler.get_auth_headers', return_value={})

        with caplog.at_level(logging.INFO, logger=blender_hand_back.__name__):
            assert blender_hand_back.hand_back(4, 'worker_hardware_changed') is True

        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == ['Job 4 reached the auto-requeue limit and was set to ERROR (worker_hardware_changed).']
        assert 'Handed job' not in caplog.text


class TestRetainedSlots:

    def _capacity(self):
        return WorkerCapacity(CapacityProfile(
            gpu_slot_count=2, cpu_slot_count=1, cpu_thread_ceiling=15,
            cpu_threads_effective=15, cards=fake_cards(2), gpu_mode='split',
        ))

    def test_a_later_claim_of_the_same_id_cannot_free_a_retained_slot(self):
        cap = self._capacity()
        cap.reserve_for_job(7, 'GPU')
        cap.retain_slots(7)  # its Blender would not die
        cap.reserve_for_job(7, 'GPU')  # requeued by the manager, claimed again

        cap.release_slot(7)  # the second claim ends

        assert cap.gpu_assignments_snapshot() == {fake_cards(1)[0].key: retained_owner(7)}
        assert retained_owner(7) < 0

    def test_retained_cpu_slot_stays_held(self):
        cap = self._capacity()
        cap.reserve_for_job(8, 'CPU')
        cap.retain_slots(8)

        cap.release_slot(8)

        assert 'CPU' not in cap.free_device_prefs()

    def test_release_reservations_re_keys_a_retained_reservation(self):
        from sethlans_worker_agent import job_resources
        from sethlans_worker_agent.executors import RendererReservation
        cap = self._capacity()
        cap.reserve_for_job(9, 'GPU')
        reservation = RendererReservation()
        reservation.retain_until_restart()

        assert job_resources.release_reservations(9, reservation, cap) is False

        assert cap.gpu_assignments_snapshot() == {fake_cards(1)[0].key: retained_owner(9)}
