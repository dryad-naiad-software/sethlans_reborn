# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``execute`` paths without processes (Unreal foundation FR-UE-2, FR-UE-11
survivors, FR-UE-16, FR-UE-17, FR-UE-25 "Report"; Conc R2-M1; chunk 7
integration requirements 2-6, 8, 12, 15).
"""
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import api_extras
from sethlans_worker_agent.api_conflict import JobNoLongerAssigned
from sethlans_worker_agent.executors.base import ExecutionContext, RendererReservation
from sethlans_worker_agent.project_archive import integrity
from sethlans_worker_agent.project_archive.errors import AbortedError, ShareUnavailableError
from sethlans_worker_agent.project_archive.layout import CacheLayout
from sethlans_worker_agent.unreal_executor import executor, job_dir, job_run, reserve, vram_gate_check
from sethlans_worker_agent.unreal_executor.delivery import INTEGRITY_MESSAGE, UPLOAD_MESSAGE, DeliveryDeps
from sethlans_worker_agent.unreal_executor.job_flow import FlowResult
from sethlans_worker_agent.unreal_executor.outcomes import Cancelled, JobFailure, LocalConflict, SurvivorsLeft
from sethlans_worker_agent.unreal_executor.pilot import PilotMetrics
from sethlans_worker_agent.unreal_executor.profile_config import EngineCvarCount
from sethlans_worker_agent.unreal_executor.runner_types import SURVIVORS_DISABLED_REASON, SURVIVORS_MESSAGE
from sethlans_worker_agent.unreal_executor.zen_guard import ZenGuardResult
from shared import owner_only_acl
from tests._unreal_worker_state import install
from tests.unit.worker.unreal._executor_fakes import params, reading


class World:
    """Fakes for one ``JobRun``: workspace, registry, flow, manager."""

    def __init__(self, tmp_path, monkeypatch, kind='render'):
        root = Path(tmp_path) / 'uecache'
        owner_only_acl.create_owner_only_dir(root)
        self.layout = CacheLayout(root)
        self.layout.ensure_children()
        self.calls = []
        self.survivors = ()
        self.flow_action = None
        self.integrity_ok = True
        self.share_error = None
        self.pilot_report = api_extras.PilotReport('done', 200)
        self.upload_result = True
        self.retained = False
        self.params = params(kind=kind)
        monkeypatch.setattr(job_run, 'UeFlow', self._flow_factory)

    # -- fakes ----------------------------------------------------------
    def _flow_factory(self, params, ctx, job, runner, summary, builders, marker, uproject, config, reading=None):
        world = self
        self.job = job

        class Flow:
            def run(self):
                return world.flow_action(job)
        return Flow()

    def prepare(self, cache, request, **kwargs):
        if self.share_error:
            raise self.share_error
        world = self

        class Ws:
            summary = {'cache': 'miss'}
            working_copy = SimpleNamespace(project_dir=Path('.'), uproject=Path('Demo.uproject'))
            entry = SimpleNamespace(entry_dir=Path(world.layout.root) / 'p' / 'x', sha256='a' * 64)
            cache = SimpleNamespace(quarantine=lambda sha, reason: world.calls.append(('quarantine', reason)))

            def finish(self, *, survivors=False, abort=None):
                world.calls.append(('finish', survivors))
                self.summary['pristine_integrity'] = 'ok' if world.integrity_ok else 'FAILED'
                return integrity.IntegrityReport(own_ok=world.integrity_ok, failures={})
        return Ws()

    def registry(self):
        world = self
        return SimpleNamespace(confirm_all=lambda: (world.calls.append(('confirm_all',)), world.survivors)[1])

    def phase_runner(self, config, registry, job, cancel, shutdown, sampler):
        return SimpleNamespace(config=config, runs=[], profile_reset=0, user_cvars=0, collisions=[], zen_ok=True)

    def upload(self, job_id, handle, filename):
        self.calls.append(('upload', filename, handle.read()))
        if isinstance(self.upload_result, Exception):
            raise self.upload_result
        return self.upload_result

    def deps(self):
        return job_run.RunDeps(
            cache=lambda: SimpleNamespace(layout=self.layout), limits=lambda: None, reachability=lambda: None,
            prepare_workspace=self.prepare, zen_scan=lambda path: ZenGuardResult(),
            engine_cvars=lambda root, names: EngineCvarCount(), sampler=lambda: None,
            registry_factory=self.registry, phase_runner=self.phase_runner,
            phase_config=lambda cache_dir, names, inst: SimpleNamespace(zen_port=8668),
            launch_context=lambda inst, cache_dir: None, builders=None, flow_config=lambda profile: None,
            get_assignment=lambda job_id: ('RENDERING', 1), worker_id=lambda: 1,
            delivery=DeliveryDeps(release_job=lambda job_id, reason: self.calls.append(('release', reason)),
                                  mark_unreachable=lambda reason: self.calls.append(('unreachable', reason)),
                                  upload=self.upload,
                                  report_pilot=lambda job_id, body: (self.calls.append(('pilot', body)),
                                                                     self.pilot_report)[1]),
            disable_until_restart=lambda reason: self.calls.append(('disable', reason)),
            keep_job_logs=lambda: 10, prep_timeout_s=lambda: 60, cancel_poll_s=lambda: 60)

    def run(self, shutdown=None):
        gate = vram_gate_check.evaluate(self.params, reading())
        reservation = reserve.UnrealReservation(self.params, install(), gate, on_release=lambda: None)
        original = reservation.retain_until_restart
        reservation.retain_until_restart = lambda: (self.calls.append(('retain',)), original())[1]
        ex = executor.UnrealExecutor(reserve_deps=executor.default_reserve_deps(), run_deps=self.deps())
        ctx = ExecutionContext(None, None, None, shutdown or threading.Event())
        self.reservation = reservation
        return ex.execute({'id': self.params.job_id, '_renderer_reservation': reservation}, ctx)

    def names(self):
        return [call[0] for call in self.calls]


def _png(job):
    path = job.output / 'sethlans_42.1000.png'
    path.write_bytes(b'\x89PNGdata')
    return FlowResult(prep=None, render_s=60.0, prep_s=20.0, warmup_s=0.0, warmup_ran=False, render_time_seconds=57,
                      outputs=[path])


def test_success_uploads_from_the_checked_handle_after_confirm_and_finish(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.flow_action = _png
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
        w.flow_action = _png
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
    w.flow_action, w.integrity_ok = _png, False
    result = w.run()
    assert result.error_message == INTEGRITY_MESSAGE and 'upload' not in w.names()


@pytest.mark.parametrize('upload,expected', [(False, 'error'), (JobNoLongerAssigned(42), 'released')])
def test_upload_outcomes(tmp_path, monkeypatch, upload, expected):
    w = World(tmp_path, monkeypatch)
    w.flow_action, w.upload_result = _png, upload
    result = w.run()
    assert (result.error_message == UPLOAD_MESSAGE) if expected == 'error' else result.released


def test_a_hardlinked_output_is_refused(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)

    def linked(job):
        flow = _png(job)
        (Path(tmp_path) / 'other.png').hardlink_to(flow.outputs[0])
        return flow
    w.flow_action = linked
    result = w.run()
    assert 'output safety checks' in result.error_message and 'upload' not in w.names()


def test_local_conflict_releases(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.flow_action = lambda job: (_ for _ in ()).throw(LocalConflict('dispatch_failed', 'port held'))
    result = w.run()
    assert result.released and ('release', 'dispatch_failed') in w.calls


def test_share_failure_releases_and_marks_unreachable(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.share_error = ShareUnavailableError('The archive is not reachable on the Sethlans share.')
    result = w.run()
    assert result.released and w.names() == ['confirm_all', 'unreachable', 'release']
    assert w.calls[2] == ('release', 'share_unavailable')


def test_cancel_causes(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.flow_action = lambda job: (_ for _ in ()).throw(Cancelled('canceled'))
    result = w.run()
    assert result.was_canceled and not result.released and 'release' not in w.names()


def test_shutdown_releases_and_skips_finish(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    shutdown = threading.Event()
    shutdown.set()
    w.flow_action = lambda job: (_ for _ in ()).throw(AbortedError('shutdown'))
    result = w.run(shutdown)
    assert result.released and ('release', 'shutdown') in w.calls and 'finish' not in w.names()
    # Review L1: the entry UE may have touched is never trusted again (no slow re-hash in the shutdown budget).
    assert ('quarantine', 'shutdown before the post-job integrity check') in w.calls
    assert 'pristine_integrity=skipped(shutdown; entry quarantined)' in result.stdout


def test_reassigned_reports_nothing(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.flow_action = lambda job: (_ for _ in ()).throw(Cancelled('reassigned'))
    result = w.run()
    assert result.released and 'release' not in w.names() and ('finish', False) in w.calls


def test_cache_unavailable_disables_unreal(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    deps = w.deps()
    deps.cache = lambda: None
    w.deps = lambda: deps
    result = w.run()
    assert 'not ready' in result.error_message and w.names()[0] == 'disable'


def _pilot_flow(job):
    metrics = PilotMetrics(peak_mb=8102, peak_source='csv', card_delta_peak_mb=7930, csv_local_used_peak_mb=8102,
                           baseline_used_mb=945, baseline_drift_mb=12, peak_phase='render', gpu_index=0,
                           max_sample_gap_s=1.2)
    prep = SimpleNamespace(pilot_frames=(0, 99, 202))
    return FlowResult(prep=prep, render_s=180.0, prep_s=24.0, warmup_s=800.0, warmup_ran=True, render_time_seconds=60,
                      metrics=metrics, gate_reading=reading())


@pytest.mark.parametrize('report,released', [('done', True), ('terminal', True), ('failed', True),
                                             ('rejected', False)])
def test_pilot_report_responses(tmp_path, monkeypatch, report, released):
    w = World(tmp_path, monkeypatch, kind='pilot')
    w.flow_action = _pilot_flow
    w.pilot_report = api_extras.PilotReport(report, 400 if report == 'rejected' else 200, 'peak_mb: too low')
    result = w.run()
    assert result.released is released and 'upload' not in w.names()
    body = next(call[1] for call in w.calls if call[0] == 'pilot')
    assert body['peak_mb'] == 8102 and body['frames'] == 3 and len(body['overrides_sha256']) == 64
    if report == 'rejected':
        assert result.error_message == 'Pilot result rejected by the manager: peak_mb: too low'


def test_pilot_failure_messages_are_prefixed(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, kind='pilot')
    w.flow_action = lambda job: (_ for _ in ()).throw(JobFailure('No camera cut found in /Game/S; cannot measure.'))
    result = w.run()
    assert result.error_message == 'Pilot render failed: No camera cut found in /Game/S; cannot measure.'


def test_execute_without_an_unreal_reservation_refuses(tmp_path, monkeypatch):
    ex = executor.UnrealExecutor(reserve_deps=executor.default_reserve_deps(), run_deps=None)
    result = ex.execute({'id': 1, '_renderer_reservation': RendererReservation()},
                        ExecutionContext(None, None, None, threading.Event()))
    assert not result.success and 'Invalid Unreal job data' in result.error_message
