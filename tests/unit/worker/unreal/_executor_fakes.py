# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared builders for the chunk 7b executor unit tests (no processes, no filesystem beyond tmp_path)."""
import copy
from types import SimpleNamespace

from sethlans_worker_agent.unreal_executor import job_params
from sethlans_worker_agent.unreal_executor.runner_types import RunOutcome, VramTrace
from sethlans_worker_agent.unreal_executor.vram import GpuMem, GpuMemoryReading
from shared.unreal.unc import validate_unc_dir

BUILD = '5.8.4-58902052'
SHA = 'a' * 64
OVERLAY_SHA = 'b' * 64
SHARE_ROOT = validate_unc_dir('\\\\nas.lan\\sethlans')
GPU_NAME = 'NVIDIA GeForce RTX 5090'
SETTINGS = {'unreal.map': '/Game/Maps/Main', 'unreal.sequence': '/Game/Seq/Edit',
            'unreal.preset': '/Game/Presets/MRG', 'unreal.output_format': 'PNG'}
OVERRIDES = {'Constraints.NewEvaluation': '1', 'r.Streaming.PoolSize': '9000',
             'r.RayTracing.ResidentGeometryMemoryPoolSizeInMB': '2048'}


def job(job_id=42, kind='render', peak=7930, frame=1000, overlay=True, **changes):
    data = {'id': job_id, 'renderer': 'UNREAL', 'unreal_kind': kind,
            'unreal_measured_peak_mb': peak if kind == 'render' else None, 'engine_build': BUILD,
            'start_frame': frame, 'end_frame': frame, 'render_settings': dict(SETTINGS),
            'asset': {'source_type': 'shared_project', 'archive_path': 'incoming/Demo.zip',
                      'uproject_path': 'Demo/Demo.uproject', 'archive_sha256': SHA,
                      'overlay_path': 'project-fixes' if overlay else None,
                      'overlay_sha256': OVERLAY_SHA if overlay else None,
                      'project_details': {'unreal_cvar_overrides': dict(OVERRIDES)}}}
    data = copy.deepcopy(data)
    data.update(changes)
    return data


def params(**kwargs):
    return job_params.validate_job_data(job(**kwargs)).with_share_root(SHARE_ROOT)


def reading(used=1000, total=32607, source='nvidia-smi', taken_at=0.0, name=GPU_NAME, gpus=None):
    gpus = gpus or (GpuMem(0, name, total, used, None, None),)
    return GpuMemoryReading(taken_at, source, tuple(gpus))


def trace(max_used=None, min_used=None, total=32607, samples=5, gap=2.0):
    t = VramTrace(samples=samples, max_gap_s=gap)
    for index, value in (max_used or {}).items():
        t.max_used[index] = value
        t.total[index] = total
    for index, value in (min_used or max_used or {}).items():
        t.min_used[index] = value
    return t


def outcome(reason='exited', exit_code=0, survivors=(), vram=None, wall_s=30.0, marker=True, quit_forced=False,
            fatal_rule='', detail='', job_object='ok', peak=None):
    return RunOutcome(exit_code=exit_code, reason=reason, rule_counts={}, wall_s=wall_s, survivors=tuple(survivors),
                      vram_peak_mb=peak, phase='render', success_marker_seen=marker, quit_forced=quit_forced,
                      fatal_rule=fatal_rule, detail=detail, job_object=job_object, vram=vram or VramTrace())


def phase_run(phase='render', pre=None, post=None, vram=None, wall_s=30.0):
    return SimpleNamespace(phase=phase, pre=pre, post=post, outcome=outcome(vram=vram, wall_s=wall_s))
