# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Builders for sequence piece payloads (sequence rendering FR-SW-1, FR-SW-2). Not a test module; names made up."""
from sethlans_worker_agent.unreal_executor import job_params
from tests.unit.worker.unreal._executor_fakes import SETTINGS, SHARE_ROOT, job

SHA = 'c' * 64


def piece_data(start=100, end=129, lead=30, fmt='PNG', shots=None, size=(1920, 1080), draft=None, resolution=None,
               **changes):
    """A piece's poll payload: kept frames ``start..end`` after ``lead`` lead-in frames, one shot by default."""
    settings = dict(SETTINGS, **{'unreal.output_format': fmt, 'unreal.shot_set_sha256': SHA,
                                 'unreal.piece_shots': [[1, start, end + 1]] if shots is None else shots})
    if size is not None:
        settings['unreal.output_size'] = list(size)
    if draft is not None:
        settings['unreal.draft'] = draft
    if resolution is not None:
        settings['unreal.resolution'] = list(resolution)
    data = job(frame=start, render_settings=settings)
    data.update(end_frame=end, unreal_lead_in_frames=lead, sequence_submission=7)
    data.update(changes)
    return data


def piece_params(**kwargs):
    return job_params.validate_job_data(piece_data(**kwargs)).with_share_root(SHARE_ROOT)


def audio_data(start=100, end=279, lead=8, shots=None, **changes):
    """An audio job's poll payload (FR-SA-1): the run ``start..end`` after ``lead`` audio lead-in frames, its shot
    parts as ``unreal.piece_shots`` (two by default), map, sequence and preset only."""
    settings = {key: SETTINGS[key] for key in ('unreal.map', 'unreal.sequence', 'unreal.preset')}
    middle = (start + end + 1) // 2
    settings['unreal.piece_shots'] = [[3, start, middle], [4, middle, end + 1]] if shots is None else shots
    data = job(kind='audio', frame=start, render_settings=settings)
    data.update(end_frame=end, unreal_lead_in_frames=lead, sequence_submission=7)
    data.update(changes)
    return data


def audio_params(**kwargs):
    return job_params.validate_job_data(audio_data(**kwargs)).with_share_root(SHARE_ROOT)
