# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The UE prep script (``ue_scripts/sethlans_prepare_queue.py``) against a stub
``unreal`` module (Unreal foundation FR-UE-8, FR-UE-19, FR-UE-25; FR-T-5).
"""
import json
import runpy
import sys

import pytest

from sethlans_worker_agent.unreal_executor import job_dir
from tests.unit.worker.unreal import _ue_stub as ue

SCRIPT = str(job_dir.SCRIPTS_SOURCE / job_dir.PREP_SCRIPT)
PRESET, SEQ = '/Game/Presets/MRG', '/Game/Seq/Edit'
# The PoC mapping (S1): frame 1000 lies in shot 02_010_0070 (edit 965-1106) whose inner cut is [101, 250).
POC_SHOT = ue.shot(965, 1106, [(101, 250)], offset_frames=101)


def run(tmp_path, monkeypatch, assets, mode='prepare', frame=1000, **world):
    world['assets'] = assets
    world.setdefault('cvars', {'r.Streaming.PoolSize': '9000'})
    monkeypatch.setitem(sys.modules, 'unreal', ue.make_unreal(world))
    params = {'schema': 1, 'job_id': 42, 'mode': mode, 'map': '/Game/Maps/Main', 'sequence': SEQ, 'preset': PRESET,
              'output_format': 'PNG', 'output_dir': 'D:\\c\\j\\42-x\\output', 'file_name_format': 'sethlans_42.{frame_number}',
              'package_root': '/Game/_SethlansFarm/Job42', 'result_path': str(tmp_path / 'result.json'),
              'cvar_names': ['r.Streaming.PoolSize'], 'max_pilot_frames': 256}
    if mode == 'prepare':
        params['frame'] = frame
    (tmp_path / 'params.json').write_text(json.dumps(params), encoding='utf-8')
    monkeypatch.setenv('SETHLANS_UE_JOB_PARAMS', str(tmp_path / 'params.json'))
    error = None
    try:
        runpy.run_path(SCRIPT, run_name='sethlans_prep')
    except RuntimeError as exc:
        error = exc
    result = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))
    return result, error, world


def poc_assets(**graph_kw):
    return {PRESET: ue.graph(**graph_kw), SEQ: ue.Sequence(0, 1928, [ue.MovieSceneCinematicShotTrack([POC_SHOT])])}


class TestPrepare:

    def test_poc_frame_through_the_shot_sections(self, tmp_path, monkeypatch):
        result, error, world = run(tmp_path, monkeypatch, poc_assets(scalability=3))
        assert error is None and result['ok'] is True, result
        assert result['queue_asset'] == '/Game/_SethlansFarm/Job42/Q_Job42' and result['preset_kind'] == 'graph'
        assert result['playback_range'] == [0, 1928] and result['camera_cut_starts'] == [965]
        assert result['effective_cvars'] == {'r.Streaming.PoolSize': '9000'} and result['scalability'] == 3
        assert result['queue_jobs'] == 1 and world['searched'] is True
        dup = world['assets']['/Game/_SethlansFarm/Job42/MRG_Job42_0']
        settings = dup.branches['Globals'][0].props
        assert settings['custom_playback_range_start'].value == 1000 and settings['custom_playback_range_end'].value == 1001
        assert settings['output_directory'].path == 'D:\\c\\j\\42-x\\output'
        png = dup.get_nodes_for_branch(ue.MovieGraphImageSequenceOutputNode_PNG, 'DefaultLayer', False)[0]
        assert png.props['file_name_format'] == 'sethlans_42.{frame_number}'
        (_name, queue), = world['queues']
        assert queue.jobs[0].graph is dup and queue.jobs[0].map == '/Game/Maps/Main'

    def test_camera_cut_starts_are_edit_frames(self, tmp_path, monkeypatch):
        shots = [ue.shot(0, 99, [(10, 109)], offset_frames=10), ue.shot(99, 202, [(0, 50), (60, 120)])]
        assets = {PRESET: ue.graph(), SEQ: ue.Sequence(0, 1928, [ue.MovieSceneCinematicShotTrack(shots)])}
        result, _error, _world = run(tmp_path, monkeypatch, assets, frame=50)
        assert result['camera_cut_starts'] == [0, 99, 159]

    def test_frame_outside_every_cut_fails_and_re_raises(self, tmp_path, monkeypatch):
        result, error, _world = run(tmp_path, monkeypatch, poc_assets(), frame=1150)
        assert isinstance(error, RuntimeError) and result['ok'] is False
        assert result['error'] == f'Frame 1150 is not inside a camera cut of {SEQ}.'

    def test_inner_frame_outside_the_shots_cut_fails(self, tmp_path, monkeypatch):
        late = ue.shot(965, 1106, [(101, 120)], offset_frames=101)  # frame 1000 -> inner 136, outside [101, 120)
        assets = {PRESET: ue.graph(), SEQ: ue.Sequence(0, 1928, [ue.MovieSceneCinematicShotTrack([late])])}
        result, error, _world = run(tmp_path, monkeypatch, assets)
        assert error is not None and 'not inside a camera cut' in result['error']

    @pytest.mark.parametrize('graph_kw,message', [
        ({'subgraphs': ['/Game/Sub']}, 'Preset uses subgraphs; phase 1 does not rewrite subgraph outputs.'),
        ({'layers': 2}, 'Preset renders 2 layers; phase 1 supports one.'),
        ({'passes': 2}, 'Preset renders 2 layers; phase 1 supports one.'),
        ({'png': False}, 'Preset does not output PNG; phase 1 does not add outputs.'),
    ])
    def test_preset_rejections(self, tmp_path, monkeypatch, graph_kw, message):
        result, error, _world = run(tmp_path, monkeypatch, poc_assets(**graph_kw))
        assert error is not None and result['ok'] is False and result['error'] == message
        assert 'queue_asset' not in result  # nothing past the failed check

    def test_other_outputs_are_disabled(self, tmp_path, monkeypatch):
        exr = ue.MovieGraphImageSequenceOutputNode_EXR()
        result, _error, world = run(tmp_path, monkeypatch, poc_assets(extra_outputs=[exr]))
        dup = world['assets']['/Game/_SethlansFarm/Job42/MRG_Job42_0']
        assert result['ok'] and dup.branches['DefaultLayer'][-1].is_disabled() and not exr.is_disabled()

    def test_existing_package_root_and_missing_preset(self, tmp_path, monkeypatch):
        result, _error, _world = run(tmp_path, monkeypatch, poc_assets(), existing_root=True)
        assert result['error'] == 'Package root /Game/_SethlansFarm/Job42 already exists.'
        result, _error, _world = run(tmp_path, monkeypatch, {})
        assert result['error'] == f'Preset {PRESET} not found.'


class TestPilotFrames:

    def test_own_cut_track_overlapping_and_outside_the_range(self, tmp_path, monkeypatch):
        cuts = [ue.Section(-5, 3), ue.Section(3, 10), ue.Section(10, 20), ue.Section(40, 50),
                ue.Section(20, 25, active=False)]
        assets = {PRESET: ue.graph(), SEQ: ue.Sequence(0, 30, [ue.MovieSceneCameraCutTrack(cuts)])}
        result, error, world = run(tmp_path, monkeypatch, assets, mode='prepare_pilot')
        assert error is None and result['pilot_frames'] == [0, 3, 10] and result['queue_jobs'] == 3
        (_name, queue), = world['queues']
        assert len(queue.jobs) == 3
        starts = [world['assets'][f'/Game/_SethlansFarm/Job42/MRG_Job42_{i}'].branches['Globals'][0]
                  .props['custom_playback_range_start'].value for i in range(3)]
        assert starts == [0, 3, 10]

    def test_shot_sections(self, tmp_path, monkeypatch):
        shots = [ue.shot(-10, 20, [(0, 100)]),            # starts before the range: kept, at the range start
                 ue.shot(20, 60, [(15, 100)]),             # first qualifying edit frame is 35
                 ue.shot(2000, 2100, [(0, 100)]),          # entirely outside the range: dropped
                 POC_SHOT]
        assets = {PRESET: ue.graph(), SEQ: ue.Sequence(0, 1928, [ue.MovieSceneCinematicShotTrack(shots)])}
        result, error, _world = run(tmp_path, monkeypatch, assets, mode='prepare_pilot')
        assert error is None and result['pilot_frames'] == [0, 35, 965]

    def test_zero_frames(self, tmp_path, monkeypatch):
        assets = {PRESET: ue.graph(), SEQ: ue.Sequence(0, 30, [])}
        result, error, _world = run(tmp_path, monkeypatch, assets, mode='prepare_pilot')
        assert error is not None and result['error'] == f'No camera cut found in {SEQ}; cannot measure.'

    def test_257_frames(self, tmp_path, monkeypatch):
        cuts = [ue.Section(i, i + 1) for i in range(257)]
        assets = {PRESET: ue.graph(), SEQ: ue.Sequence(0, 300, [ue.MovieSceneCameraCutTrack(cuts)])}
        result, error, _world = run(tmp_path, monkeypatch, assets, mode='prepare_pilot')
        assert error is not None and result['error'] == 'Sequence has 257 shots; the pilot supports at most 256.'
        assert len(result['pilot_frames']) == 257
