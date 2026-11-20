# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``RangeFlow``, a sequence piece's UE phases, against a fake phase runner that writes the files UE would
(sequence rendering FR-SW-3, FR-SW-5, FR-SW-6, FR-SW-7, FR-SW-9, FR-SW-11), and the prep params of sequence
pilots and standalone jobs (D8, FR-SR-1).
"""
import json
import os
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional

import pytest

from sethlans_worker_agent.unreal_executor import queue_prep, sequence_flow
from sethlans_worker_agent.unreal_executor.adapter_choice import Choice
from sethlans_worker_agent.unreal_executor.job_flow import FlowConfig
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure, LocalConflict
from sethlans_worker_agent.unreal_executor.summary import Summary
from sethlans_worker_agent.unreal_executor.vram import GpuMem
from tests.unit.worker.unreal._executor_fakes import SETTINGS, outcome, params, reading
from tests.unit.worker.unreal.sequence._pieces import piece_params

KEY = 'pci:0000:01:00.0'
CARD = GpuMem(0, 'Example GPU 32G', 32607, 1000, None, None, KEY)
P = '[2026.11.20-10.00.00:000][  0]'
RENDER_LOG = [P + 'LogMovieRenderPipeline: Movie Graph Render completed. Duration: +00:00:05.250',
              P + 'LogMovieRenderPipeline: MoviePipelineLinearExecutorBase finished 1 jobs in +00:00:09.0.',
              P + 'LogCore: FPlatformMisc::RequestExitWithStatus(0, 0, <NoCallSiteInfo>)']


@dataclass(frozen=True)
class Ctx:
    graphics_adapter: Optional[int] = None


class Runner:
    """Writes the prep result, nothing for the warm-up, and the frames (1 s apart) and log of the render."""

    def __init__(self, job, p, prep_extra=None, skip=(), cancel_first=False):
        self.job, self.p, self.launches = job, p, []
        self.prep_extra, self.skip, self.runs, self.cancel_first = prep_extra or {}, set(skip), [], cancel_first

    def launch(self, phase, attempt, launch, **kwargs):
        self.launches.append((phase, attempt, launch, kwargs))
        log = self.job.log(phase, attempt)
        if phase == 'prep':
            result = {'ok': True, 'queue_asset': f'/Game/_SethlansFarm/Job{self.p.job_id}/Q_Job{self.p.job_id}',
                      'preset_kind': 'graph', 'playback_range': [0, 1000], 'camera_cut_starts': [0, 80, 115, 500],
                      'effective_cvars': {}, 'queue_jobs': 1, 'warnings': []}
            result.update(self.prep_extra)
            self.job.prep_result.write_text(json.dumps(result), encoding='utf-8')
        if phase == 'render':
            piece, ext = self.p.piece, 'exr' if self.p.output_format == 'EXR' else 'png'
            for n, frame in enumerate(range(piece.first, piece.end + 1)):
                if frame not in self.skip:
                    path = self.job.output / f'sethlans_{self.p.job_id}.{frame:04d}.{ext}'
                    path.write_bytes(b'x')
                    os.utime(path, (2000.0 + n, 2000.0 + n))
            lines = list(RENDER_LOG)
            if self.cancel_first and attempt == 1:  # FR-UE-10's retry; attempt 1 also leaves a stray frame
                lines.append(P + 'LogComputeFramework: Warning: X: Compilation cancelled.')
                (self.job.output / f'sethlans_{self.p.job_id}.0999.png').write_bytes(b'x')
            log.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return SimpleNamespace(phase=phase, outcome=outcome(wall_s=12.0, peak=7500), log_path=log)


def _flow(tmp_path, p, **runner_kw):
    for name in ('output', 'logs', 'scripts'):
        (tmp_path / name).mkdir(exist_ok=True)
    job = SimpleNamespace(params=tmp_path / 'params.json', prep_result=tmp_path / 'prep_result.json',
                          warmup_params=tmp_path / 'warmup_params.json', warmup_result=tmp_path / 'warmup_result.json',
                          scripts=tmp_path / 'scripts', output=tmp_path / 'output', logs=tmp_path / 'logs',
                          script=lambda name: tmp_path / 'scripts' / name,
                          log=lambda phase, attempt: tmp_path / 'logs' / f'{phase}.{attempt}.log')
    runner = Runner(job, p, **runner_kw)
    builders = SimpleNamespace(build_prep=lambda *a: ('prep', a), build_warmup=lambda *a: ('warmup', a),
                               build_render=lambda *a, **k: ('render', a))
    config = FlowConfig(step_frames=24, step_seconds=1, hold_seconds=1, quit_grace_s=5.0, log_max_bytes=1 << 20,
                        profile=str(tmp_path / 'profile'))
    flow = sequence_flow.RangeFlow(p, Ctx(), job, runner, Summary(), builders, tmp_path / 'marker.json',
                                   'Demo.uproject', config, reading=reading(gpus=(CARD,)),
                                   choose=lambda job_id, key=None: Choice(job_id, KEY, CARD.name, 1), first_key=KEY)
    flow.reserve_gb = lambda: 0
    return flow, runner, job


def _json(path):
    return json.loads(path.read_text(encoding='utf-8'))


class TestRun:

    def test_prep_warm_up_render_and_output(self, tmp_path):
        p = piece_params(shots=[[3, 100, 115], [4, 115, 130]], draft={'screen_percentage': 50, 'motion_blur': False})
        flow, runner, job = _flow(tmp_path, p)
        result = flow.run()
        assert [phase for phase, *_rest in runner.launches] == ['prep', 'warmup', 'render']
        params_file = _json(job.params)
        assert (params_file['mode'], params_file['range'], params_file['start'], params_file['output_format']) == \
            ('prepare_range', [70, 130], 100, 'PNG')
        assert params_file['draft'] == {'motion_blur': False, 'screen_percentage': 50} and 'frame' not in params_file
        assert params_file['draft_cvars'] == ['r.ScreenPercentage', 'r.MotionBlurQuality']
        assert 'resolution' not in params_file
        assert _json(job.warmup_params)['frames'] == [70, 80, 100, 115]  # FR-SW-6: the lead-in's shot (cut at 80)
        assert [path.name for path in result.outputs] == [f'sethlans_42.{n:04d}.png' for n in range(100, 130)]
        assert sorted(os.listdir(job.output)) == [f'sethlans_42.{n:04d}.png' for n in range(100, 130)]
        piece = result.piece
        assert piece.frames == tuple(range(100, 130)) and piece.card is CARD and piece.vram_peak_mb == 7500
        assert [(row['index'], row['frames'], row['seconds']) for row in piece.timing.shots] == [(3, 15, 15.0),
                                                                                                 (4, 15, 15.0)]
        assert piece.timing.lead_in_s is not None
        s = flow.summary
        assert (s.get('piece'), s.get('lead_in'), s.get('lead_in_deleted'), s.get('output')) == \
            ('100-129', '30', '30', '30 PNG frames')

    def test_the_render_retry_starts_from_an_empty_output_folder(self, tmp_path):
        """Review S4: attempt 1's files never reach the retry's collection."""
        flow, runner, job = _flow(tmp_path, piece_params(start=10, end=11, lead=1), cancel_first=True)
        result = flow.run()
        assert [(phase, attempt) for phase, attempt, *_ in runner.launches] == [
            ('prep', 1), ('warmup', 1), ('render', 1), ('warmup', 2), ('render', 2)]
        assert [path.name for path in result.outputs] == ['sethlans_42.0010.png', 'sethlans_42.0011.png']
        assert sorted(os.listdir(job.output)) == ['sethlans_42.0010.png', 'sethlans_42.0011.png']

    def test_the_motion_blur_path_is_in_the_summary(self, tmp_path):
        p = piece_params(draft={'motion_blur': False})
        flow, _runner, job = _flow(tmp_path, p, prep_extra={'motion_blur_off': 'samples1_pp_off'})
        flow.prep()
        assert flow.summary.get('motion_blur_off') == 'samples1_pp_off'
        assert _json(job.params)['draft_cvars'] == ['r.MotionBlurQuality']
        flow, _runner, _job = _flow(tmp_path, p, prep_extra={'motion_blur_off': 'C:\\x'})
        flow.prep()
        assert flow.summary.get('motion_blur_off') == 'unknown'

    def test_a_pieces_warm_up_visits_only_its_own_frames(self, tmp_path):
        """By-hand finding: the warm-up stepped the whole sequence (101 visits) for a 3-frame piece."""
        flow, _runner, job = _flow(tmp_path, piece_params(start=150, end=152, lead=0, shots=[[2, 150, 153]]))
        flow.warm_up(flow.prep(), 1)
        data = _json(job.warmup_params)
        assert data['only_frames'] is True and data['frames'] == [150]
        standalone = queue_prep.warmup_params(params(), flow.prep(), 'r', 24, 4, 240)
        assert standalone['only_frames'] is False and standalone['frames'] == [1000]

    def test_a_missing_frame_fails_the_output_step(self, tmp_path):
        flow, _runner, job = _flow(tmp_path, piece_params(), skip={75})
        with pytest.raises(JobFailure, match=r"missing \['sethlans_42.0075.png'\]"):
            flow.run()
        assert len(os.listdir(job.output)) == 59  # nothing deleted when the check fails

    def test_a_draft_cvar_the_preset_sets_itself_is_a_warning(self, tmp_path):
        p = piece_params(draft={'screen_percentage': 50})
        flow, _runner, _job = _flow(tmp_path, p, prep_extra={'draft_conflicts': ['r.screenpercentage']})
        flow.prep()
        assert flow.summary.get('draft_conflicts') == 'r.ScreenPercentage'
        assert list(flow.summary.warnings()) == [sequence_flow.DRAFT_CONFLICT.format(name='r.ScreenPercentage')]

    @pytest.mark.parametrize('conflicts', [['r.Other.Name'], 'r.ScreenPercentage', ['r.ScreenPercentage'] * 17, [5]])
    def test_a_conflict_list_outside_the_sent_draft_cvars_is_an_invalid_result(self, tmp_path, conflicts):
        flow, _runner, _job = _flow(tmp_path, piece_params(draft={'screen_percentage': 50}),
                                    prep_extra={'draft_conflicts': conflicts})
        with pytest.raises(JobFailure, match=r'Prep pass returned an invalid result \(draft_conflicts\)\.'):
            flow.prep()


class TestDisk:

    def test_a_piece_that_does_not_fit_is_released_before_the_prep(self, tmp_path):
        flow, runner, _job = _flow(tmp_path, piece_params(size=(2560, 1440)))
        flow.reserve_gb = lambda: 10 ** 9
        with pytest.raises(LocalConflict) as caught:
            flow.run()
        assert caught.value.reason == 'local_conflict' and runner.launches == []
        assert 'needs up to 0.8 GB for its 60 frames' in caught.value.message
        assert 'above the 1000000000 GB reserve' in caught.value.message

    def test_the_estimate(self, tmp_path):
        piece = piece_params(size=(2560, 1440)).piece
        assert sequence_flow.disk_need(piece, 'png') == 60 * 2560 * 1440 * 4
        assert sequence_flow.disk_need(piece, 'exr') == 60 * 2560 * 1440 * 8
        need = 60 * 2560 * 1440 * 8
        assert sequence_flow.check_disk(piece, 'exr', tmp_path, 1, free_bytes=need + sequence_flow.GIB) == need
        with pytest.raises(LocalConflict):
            sequence_flow.check_disk(piece, 'exr', tmp_path, 1, free_bytes=need + sequence_flow.GIB - 1)
        assert sequence_flow.check_disk(piece_params(size=None).piece, 'png', tmp_path, 10 ** 9) is None

    def test_frames_other_job_folders_still_hold_count_as_free(self, tmp_path):
        """Review S9: a failed piece's frames wait for their cleanup after its card is freed."""
        jobs = tmp_path / 'j'
        for name, files in (('7-aaaa', {'a.png': 300, 'b.png': 700}), ('8-bbbb', {'c.exr': 50}), ('9-own', {'d': 9})):
            (jobs / name / 'output').mkdir(parents=True)
            for file_name, size in files.items():
                (jobs / name / 'output' / file_name).write_bytes(b'x' * size)
        (jobs / '6-logs-only').mkdir()
        assert sequence_flow.leftover_bytes(jobs, jobs / '9-own') == 1050
        piece = piece_params(start=0, end=0, lead=0, size=(10, 10)).piece  # 400 bytes as PNG
        with pytest.raises(LocalConflict):
            sequence_flow.check_disk(piece, 'png', tmp_path, 0, free_bytes=399)
        assert sequence_flow.check_disk(piece, 'png', tmp_path, 0, free_bytes=0, leftover=400) == 400


class TestWarmFrames:

    def test_the_lead_in_start_the_shot_starts_and_the_cuts_inside_the_range(self):
        piece = piece_params(start=100, end=129, lead=30, shots=[[3, 100, 115], [4, 115, 130]]).piece
        assert sequence_flow.warm_frames(piece, [0, 50, 80, 115, 130, 500]) == [70, 80, 100, 115]

    def test_at_most_64(self):
        shots = [[i + 1, i, i + 1] for i in range(100)]
        piece = piece_params(start=0, end=99, lead=0, shots=shots).piece
        assert sequence_flow.warm_frames(piece, []) == list(range(64))


class TestPilotAndStandaloneParams:

    def test_a_sequence_pilot_asks_for_its_frames_and_resolution(self, tmp_path):
        settings = dict(SETTINGS, **{'unreal.pilot_frames': [0, 25, 30], 'unreal.resolution': [640, 360]})
        p = params(kind='pilot', render_settings=settings)
        data = queue_prep.prep_params(p, 'out', 'res', 'scripts')
        assert (data['mode'], data['pilot_frames'], data['resolution']) == ('prepare_pilot', [0, 25, 30], [640, 360])
        result = {'ok': True, 'queue_asset': '/Game/_SethlansFarm/Job42/Q_Job42', 'preset_kind': 'graph',
                  'playback_range': [0, 100], 'pilot_frames': [0, 25, 30], 'queue_jobs': 3}
        (tmp_path / 'r.json').write_text(json.dumps(result), encoding='utf-8')
        assert queue_prep.read_result(tmp_path / 'r.json', p).pilot_frames == (0, 25, 30)
        result.update(pilot_frames=[0, 25], queue_jobs=2)
        (tmp_path / 'r.json').write_text(json.dumps(result), encoding='utf-8')
        with pytest.raises(queue_prep.PrepError, match=r'\(pilot_frames\)'):
            queue_prep.read_result(tmp_path / 'r.json', p)

    def test_standalone_jobs_keep_their_params(self):
        data = queue_prep.prep_params(params(), 'out', 'res', 'scripts')
        assert data['frame'] == 1000 and 'resolution' not in data and 'pilot_frames' not in data
        sized = params(render_settings=dict(SETTINGS, **{'unreal.resolution': [1280, 720]}))
        assert queue_prep.prep_params(sized, 'out', 'res', 'scripts')['resolution'] == [1280, 720]
        assert 'pilot_frames' not in queue_prep.prep_params(params(kind='pilot'), 'out', 'res', 'scripts')
