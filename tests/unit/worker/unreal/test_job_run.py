# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``execute`` paths without processes: success, survivors, failures and the
output checks (Unreal foundation FR-UE-2, FR-UE-11 survivors, FR-UE-16,
FR-UE-17; Conc R2-M1; chunk 7 integration requirements 2-6, 8, 12, 15).

Releases, cancels and shutdown are in ``test_job_run_release.py``; pilots
in ``test_job_run_pilot.py``; the fakes in ``_job_run_world.py``.
"""
import threading
from pathlib import Path

import pytest

from sethlans_worker_agent.api_conflict import JobNoLongerAssigned
from sethlans_worker_agent.executors.base import ExecutionContext, RendererReservation
from sethlans_worker_agent.unreal_executor import executor, job_dir
from sethlans_worker_agent.unreal_executor.delivery import INTEGRITY_MESSAGE, UPLOAD_MESSAGE
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure, SurvivorsLeft
from sethlans_worker_agent.unreal_executor.runner_types import SURVIVORS_DISABLED_REASON, SURVIVORS_MESSAGE
from tests.unit.worker.unreal._job_run_world import World, png_flow


def test_success_uploads_from_the_checked_handle_after_confirm_and_finish(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.flow_action = png_flow
    result = w.run()
    assert result.success and not result.released and result.output_path is None and result.render_time_seconds == 57
    assert w.names() == ['confirm_all', 'finish', 'upload'] and w.calls[2][1:] == ('sethlans_42.1000.png', b'\x89PNGdata')
    assert w.calls[1] == ('finish', False)
    assert not (w.job.output / 'sethlans_42.1000.png').exists()
    result.after_report()
    assert not w.job.output.exists() and w.job.root.exists()
    assert '[sethlans-unreal] pristine_integrity=ok' in result.stdout


@pytest.mark.parametrize('source', ['confirm_all', 'phase'])
def test_survivors_retain_disable_and_quarantine(tmp_path, monkeypatch, source):
    w = World(tmp_path, monkeypatch)
    if source == 'confirm_all':
        w.survivors = ((5, 'UnrealEditor-Cmd.exe'),)
        w.flow_action = png_flow
    else:
        w.flow_action = lambda job: (_ for _ in ()).throw(SurvivorsLeft(((5, 'x'),)))
    result = w.run()
    assert not result.success and result.error_message == SURVIVORS_MESSAGE
    assert w.names()[:4] == ['confirm_all', 'retain', 'disable', 'finish']
    assert ('disable', SURVIVORS_DISABLED_REASON) in w.calls and ('finish', True) in w.calls
    assert 'upload' not in w.names() and w.reservation.retained


def test_an_unexpected_error_is_an_error_report_with_cleanup(tmp_path, monkeypatch):
    """Review L4/W4: a bug still gets exit confirmation, finish, an ERROR report and the post-report cleanup."""
    w = World(tmp_path, monkeypatch)
    w.flow_action = lambda job: 1 / 0
    result = w.run()
    assert not result.success and result.error_message == 'Unreal job failed unexpectedly (ZeroDivisionError).'
    assert w.names() == ['confirm_all', 'finish'] and result.after_report is not None
    result.after_report()
    assert not w.job.output.exists() and job_dir.active_names() == frozenset()


def test_base_exceptions_still_propagate_after_cleanup(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.flow_action = lambda job: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        w.run()
    assert w.names() == ['confirm_all', 'finish']


def test_job_object_is_skipped_without_launches(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.flow_action = lambda job: (_ for _ in ()).throw(JobFailure('Project config sets a Zen/DDC data path'))
    result = w.run()
    assert '[sethlans-unreal] job_object=skipped' in result.stdout


def test_integrity_failure_discards_the_output(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.flow_action, w.integrity_ok = png_flow, False
    result = w.run()
    assert result.error_message == INTEGRITY_MESSAGE and 'upload' not in w.names()


@pytest.mark.parametrize('upload,expected', [(False, 'error'), (JobNoLongerAssigned(42), 'released')])
def test_upload_outcomes(tmp_path, monkeypatch, upload, expected):
    w = World(tmp_path, monkeypatch)
    w.flow_action, w.upload_result = png_flow, upload
    result = w.run()
    assert (result.error_message == UPLOAD_MESSAGE) if expected == 'error' else result.released


def test_a_hardlinked_output_is_refused(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)

    def linked(job):
        flow = png_flow(job)
        (Path(tmp_path) / 'other.png').hardlink_to(flow.outputs[0])
        return flow
    w.flow_action = linked
    result = w.run()
    assert 'output safety checks' in result.error_message and 'upload' not in w.names()


def test_execute_without_an_unreal_reservation_refuses(tmp_path, monkeypatch):
    ex = executor.UnrealExecutor(reserve_deps=executor.default_reserve_deps(), run_deps=None)
    result = ex.execute({'id': 1, '_renderer_reservation': RendererReservation()},
                        ExecutionContext(None, None, None, threading.Event()))
    assert not result.success and 'Invalid Unreal job data' in result.error_message
