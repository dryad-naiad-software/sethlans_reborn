# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The fakes for one ``JobRun`` without processes (workspace, registry, flow,
manager), shared by the ``test_job_run*`` modules.
"""
import threading
from pathlib import Path
from types import SimpleNamespace

from sethlans_worker_agent import api_extras
from sethlans_worker_agent.executors.base import ExecutionContext
from sethlans_worker_agent.project_archive import integrity
from sethlans_worker_agent.project_archive.layout import CacheLayout
from sethlans_worker_agent.unreal_executor import executor, job_run, reserve, vram_gate_check
from sethlans_worker_agent.unreal_executor.delivery import DeliveryDeps
from sethlans_worker_agent.unreal_executor.job_flow import FlowResult
from sethlans_worker_agent.unreal_executor.pilot import PilotMetrics
from sethlans_worker_agent.unreal_executor.profile_config import EngineCvarCount
from sethlans_worker_agent.unreal_executor.vram import GpuMem
from sethlans_worker_agent.unreal_executor.zen_guard import ZenGuardResult
from shared import owner_only_acl
from tests._unreal_worker_state import install
from tests.unit.worker.unreal._executor_fakes import KIND, params, reading


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
        self.shots_report = api_extras.PilotReport('done', 200)
        self.piece_report = api_extras.PilotReport('done', 200)
        self.frame_reports = []  # one PilotReport per frames/ call, in order; 'done' when empty
        self.upload_result = True
        self.retained = False
        self.params = params(kind=kind)
        self.reading = reading()
        self.card_key = None  # the held card (render per card D1); None: the gate's card
        monkeypatch.setattr(job_run, 'UeFlow', self._flow_factory)

    # -- fakes ----------------------------------------------------------
    def _flow_factory(self, params, ctx, job, runner, summary, builders, marker, uproject, config, reading=None,
                      choose=None, first_key=None):
        world = self
        self.job = job
        self.choose = choose
        self.first_key = first_key
        self.runner = runner

        class Flow:
            def run(self):
                return world.flow_action(job)
        self.flow = Flow()  # job_run sets its ``disk`` (render per card FR-PC-DSK-1)
        return self.flow

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

    def upload_frame(self, job_id, frame, handle, filename):
        self.calls.append(('frame', frame, filename, handle.read()))
        return self.frame_reports.pop(0) if self.frame_reports else api_extras.PilotReport('done', 200)

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
                                                                     self.pilot_report)[1],
                                  report_shots=lambda job_id, body: (self.calls.append(('shots', job_id, body)),
                                                                     self.shots_report)[1],
                                  upload_frame=self.upload_frame,
                                  report_piece=lambda job_id, body: (self.calls.append(('piece', job_id, body)),
                                                                     self.piece_report)[1],
                                  upload_sound=lambda *args, **kw: None),
            disable_until_restart=lambda reason: self.calls.append(('disable', reason)),
            keep_job_logs=lambda: 10, prep_timeout_s=lambda: 60, cancel_poll_s=lambda: 60)

    def run(self, shutdown=None):
        gate = vram_gate_check.evaluate(self.params, self.reading)
        reservation = reserve.UnrealReservation(self.params, install(), gate, on_release=lambda: None,
                                                card_key=self.card_key)
        original = reservation.retain_until_restart
        reservation.retain_until_restart = lambda: (self.calls.append(('retain',)), original())[1]
        ex = executor.UnrealExecutor(reserve_deps=executor.default_reserve_deps(), run_deps=self.deps())
        ctx = ExecutionContext(None, None, None, shutdown or threading.Event())
        self.reservation = reservation
        return ex.execute({'id': self.params.job_id, '_renderer_reservation': reservation}, ctx)

    def names(self):
        return [call[0] for call in self.calls]


def png_flow(job):
    path = job.output / 'sethlans_42.1000.png'
    path.write_bytes(b'\x89PNGdata')
    return FlowResult(prep=None, render_s=60.0, prep_s=20.0, warmup_s=0.0, warmup_ran=False, render_time_seconds=57,
                      outputs=[path])


def pilot_flow(job, card_key='pci:0000:01:00.0'):
    metrics = PilotMetrics(peak_mb=8102, peak_source='csv', card_delta_peak_mb=7930, csv_local_used_peak_mb=8102,
                           baseline_used_mb=945, baseline_drift_mb=12, peak_phase='render', gpu_index=0,
                           max_sample_gap_s=1.2)
    prep = SimpleNamespace(pilot_frames=(0, 99, 202), game_overrides='implicit', texture_streaming='off')
    gpu = GpuMem(0, 'NVIDIA GeForce RTX 5090', 32607, 1000, None, None, card_key, card_kind=KIND)
    return FlowResult(prep=prep, render_s=180.0, prep_s=24.0, warmup_s=800.0, warmup_ran=True, render_time_seconds=60,
                      metrics=metrics, gate_reading=reading(gpus=(gpu,)), card=gpu)
