# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Harness for the executor integration tests: a real cache under ``tmp_path``,
a local directory standing in for the share, the fake engine, a fake GPU
sampler and a fake manager (cancel watcher, release, upload, pilot-result).
"""
import functools
import json
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from sethlans_worker_agent import api_extras
from sethlans_worker_agent.executors.base import ExecutionContext
from sethlans_worker_agent.project_archive import workspace
from sethlans_worker_agent.unreal_executor import (
    detection, executor, isolation_state, job_params, reserve, startup_recovery, user_state, vram_gate_check)
from sethlans_worker_agent.unreal_executor.delivery import DeliveryDeps
from sethlans_worker_agent.unreal_executor.vram import GpuMem, GpuMemoryReading
from tests.integration.worker.project_archive._helpers import (
    SHARE_ROOT, AlwaysReachable, limits, local_resolver, make_cache, make_zip)
from tests.integration.worker.unreal_executor._fake_ue import FAKE_UE_SCRIPT, make_fake_engine

BUILD = '5.8.4-58902052'
GPU = 'NVIDIA GeForce RTX 5090'
OVERRIDES = {'Constraints.NewEvaluation': '1', 'r.Streaming.PoolSize': '9000'}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class FakeSampler:
    """Card readings: 1,000 MB used while no fake UE runs, 3,000 MB while one does."""

    def __init__(self, exe: Path):
        self.exe = str(exe).lower()

    def _busy(self) -> bool:
        for proc in psutil.process_iter(['exe']):
            if (proc.info.get('exe') or '').lower() == self.exe:
                return True
        return False

    def reading(self):
        used = 3000 if self._busy() else 1000
        return GpuMemoryReading(time.monotonic(), 'nvidia-smi', (GpuMem(0, GPU, 32607, used, None, None),))

    def fresh(self, max_age_s, deadline):
        return self.reading()

    def latest(self):
        return self.reading()


@dataclass
class FakeManager:
    releases: list = field(default_factory=list)
    uploads: list = field(default_factory=list)
    pilot_bodies: list = field(default_factory=list)
    status: str = 'RENDERING'
    pilot_report: object = None

    def deps(self) -> DeliveryDeps:
        return DeliveryDeps(release_job=lambda job_id, reason: self.releases.append((job_id, reason)) or True,
                            mark_unreachable=lambda reason: None, upload=self.upload, report_pilot=self.report)

    def upload(self, job_id, handle, filename):
        self.uploads.append((job_id, filename, handle.read()))
        return True

    def report(self, job_id, body):
        self.pilot_bodies.append(body)
        return self.pilot_report or api_extras.PilotReport('done', 200)


class Harness:

    def __init__(self, tmp_path: Path, monkeypatch, opts=None):
        self.tmp = Path(tmp_path)
        self.cache = make_cache(self.tmp)
        startup_recovery.mark_ready_for_tests(self.cache)
        engine = self.tmp / 'UE_5.8'
        self.exe = make_fake_engine(engine)
        self.install = detection.EngineInstall(root=engine, editor_cmd=self.exe, build=BUILD, supported=True,
                                               reason='', source='config')
        self.share = self.tmp / 'share'
        (self.share / 'incoming').mkdir(parents=True)
        files = {'Demo/Demo.uproject': FAKE_UE_SCRIPT.encode('utf-8'),
                 'Demo/fake_ue.json': json.dumps(opts or {}).encode('utf-8'),
                 'Demo/Config/DefaultEngine.ini': b'[/Script/Engine.RendererSettings]\nr.X=1\n'}
        self.sha = make_zip(self.share / 'incoming' / 'demo.zip', files)
        self.port = free_port()
        monkeypatch.setattr(executor.config_unreal, 'ZEN_PORT', self.port)
        profile = user_state.profile_dir(self.cache.layout.root)
        user_state.ensure_profile(profile)
        assert isolation_state.register(profile).ok
        self.sampler = FakeSampler(self.exe)
        self.manager = FakeManager()

    def job(self, job_id, kind='render', frame=1000, peak=2000):
        return {'id': job_id, 'renderer': 'UNREAL', 'unreal_kind': kind,
                'unreal_measured_peak_mb': peak if kind == 'render' else None, 'engine_build': BUILD,
                'start_frame': frame, 'end_frame': frame,
                'render_settings': {'unreal.map': '/Game/Maps/Main', 'unreal.sequence': '/Game/Seq/Edit',
                                    'unreal.preset': '/Game/Presets/MRG', 'unreal.output_format': 'PNG'},
                'asset': {'source_type': 'shared_project', 'archive_path': 'incoming/demo.zip',
                          'uproject_path': 'Demo/Demo.uproject', 'archive_sha256': self.sha,
                          'project_details': {'unreal_cvar_overrides': dict(OVERRIDES)}}}

    def run_deps(self):
        deps = executor.default_run_deps()
        deps.cache = lambda: self.cache
        deps.limits = limits
        deps.reachability = AlwaysReachable
        deps.prepare_workspace = functools.partial(workspace.prepare_workspace, resolve=local_resolver(self.share),
                                                   share_validator=lambda path: str(path))
        deps.sampler = lambda: self.sampler
        deps.delivery = self.manager.deps()
        deps.get_assignment = lambda job_id: (self.manager.status, 1)
        deps.worker_id = lambda: 1
        deps.prep_timeout_s = lambda: 300
        return deps

    def reservation(self, job_data):
        params = job_params.validate_job_data(job_data).with_share_root(SHARE_ROOT)
        gate = vram_gate_check.evaluate(params, self.sampler.reading())
        assert gate.ok, gate.message
        return reserve.UnrealReservation(params, self.install, gate, on_release=lambda: None)

    def execute(self, job_data, shutdown=None):
        job_data['_renderer_reservation'] = self.reservation(job_data)
        ex = executor.UnrealExecutor(reserve_deps=executor.default_reserve_deps(), run_deps=self.run_deps())
        ctx = ExecutionContext(None, None, None, shutdown or threading.Event())
        result = ex.execute(job_data, ctx)
        if result.after_report is not None:
            result.after_report()
        return result


def summary(result) -> dict:
    out = {}
    for line in result.stdout.splitlines():
        key, _, value = line.split(' ', 1)[1].partition('=')
        out.setdefault(key, value)
    return out
