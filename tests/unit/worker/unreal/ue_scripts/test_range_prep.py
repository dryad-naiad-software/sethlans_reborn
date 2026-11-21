# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The prep script's piece mode and the edits module against the stub ``unreal`` module (sequence rendering
FR-SW-3, FR-SW-4, D5b, D6, D8, D11, FR-SR-1, AC-38). The params are the ones the worker writes
(``sequence_prep.range_params``, ``queue_prep.prep_params``), so the scripts see exactly what production sends.
"""
import json
import runpy
import sys

import pytest

from sethlans_worker_agent.unreal_executor import job_dir, queue_prep, sequence_prep
from tests.unit.worker.unreal import _ue_stub as ue
from tests.unit.worker.unreal._executor_fakes import SETTINGS, params
from tests.unit.worker.unreal.sequence._pieces import piece_params
from tests.unit.worker.unreal.ue_scripts._discovery import run_list

SCRIPT = str(job_dir.SCRIPTS_SOURCE / job_dir.PREP_SCRIPT)
PRESET, SEQ = SETTINGS['unreal.preset'], SETTINGS['unreal.sequence']
DUP = '/Game/_SethlansFarm/Job42/MRG_Job42_0'
CUTS = ue.MovieSceneCameraCutTrack([ue.Section(0, 80), ue.Section(80, 115), ue.Section(115, 500),
                                    ue.Section(600, 1000)])  # a gap 500-600


def run(tmp_path, monkeypatch, preset, p, seq=None):
    world = {'assets': {PRESET: preset, SEQ: seq or ue.Sequence(0, 1000, [CUTS])}}
    monkeypatch.setitem(sys.modules, 'unreal', ue.make_unreal(world))
    build = sequence_prep.range_params if p.is_piece else queue_prep.prep_params
    data = build(p, 'D:\\c\\j\\42-x\\output', str(tmp_path / 'result.json'), str(job_dir.SCRIPTS_SOURCE))
    (tmp_path / 'params.json').write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setenv('SETHLANS_UE_JOB_PARAMS', str(tmp_path / 'params.json'))
    error = None
    try:
        runpy.run_path(SCRIPT, run_name='sethlans_prep')
    except Exception as exc:  # the script re-raises whatever failed it
        error = exc
    return json.loads((tmp_path / 'result.json').read_text(encoding='utf-8')), error, world


def _ok(result, error):
    assert error is None and result['ok'] is True, result


def legacy(*settings, png=True):
    out = ue.MoviePipelineOutputSetting(handle_frame_count=5, frame_number_offset=10, output_frame_step=2,
                                        zero_pad_frame_numbers=3)
    outputs = [ue.MoviePipelineImageSequenceOutput_PNG()] if png else []
    return ue.MoviePipelinePrimaryConfig([out, ue.MoviePipelineRenderPass()] + outputs + list(settings))


def _globals(world):
    return world['assets'][DUP].branches['Globals'][0].props


class TestGraphPiece:

    def test_one_duplicate_over_the_whole_range_with_d5b_settings(self, tmp_path, monkeypatch):
        preset = ue.graph()
        original = dict(preset.branches['Globals'][0].props)
        result, error, world = run(tmp_path, monkeypatch, preset, piece_params())
        _ok(result, error)
        assert result['queue_jobs'] == 1 and 'draft_conflicts' not in result
        props = _globals(world)
        assert (props['custom_playback_range_start'].value, props['custom_playback_range_end'].value) == (70, 130)
        assert (props['override_handle_frame_count'], props['handle_frame_count']) == (True, 0)
        assert (props['override_frame_number_offset'], props['frame_number_offset']) == (True, 0)
        assert (props['override_zero_pad_frame_numbers'], props['zero_pad_frame_numbers']) == (True, 4)
        assert 'output_frame_step' not in props  # the 5.8.4 node has none (U-1): left alone
        assert preset.branches['Globals'][0].props == original  # the artist's preset is never touched
        (_name, queue), = world['queues']
        assert len(queue.jobs) == 1 and queue.jobs[0].graph is world['assets'][DUP]
        assert world['saved'][0] is world['assets'][DUP] and preset not in world['saved']

    def test_a_preset_zero_pad_above_4_is_kept(self, tmp_path, monkeypatch):
        preset = ue.graph()
        preset.branches['Globals'][0].props.update(override_zero_pad_frame_numbers=True, zero_pad_frame_numbers=6)
        result, error, world = run(tmp_path, monkeypatch, preset, piece_params())
        _ok(result, error)
        assert _globals(world)['zero_pad_frame_numbers'] == 6

    def test_exr_enables_the_presets_exr_node_and_disables_png(self, tmp_path, monkeypatch):
        exr = ue.MovieGraphImageSequenceOutputNode_EXR(disabled=True)
        result, error, world = run(tmp_path, monkeypatch, ue.graph(extra_outputs=[exr]), piece_params(fmt='EXR'))
        _ok(result, error)
        png_dup, exr_dup = world['assets'][DUP].branches['DefaultLayer'][-2:]
        assert png_dup.is_disabled() and not exr_dup.is_disabled() and exr.is_disabled()
        assert exr_dup.props['file_name_format'] == 'sethlans_42.{frame_number}'

    def test_exr_without_an_exr_node_fails(self, tmp_path, monkeypatch):
        result, error, world = run(tmp_path, monkeypatch, ue.graph(), piece_params(fmt='EXR'))
        assert error is not None and result['error'] == "The preset has no EXR output node; add one to render EXR."
        assert 'queue_asset' not in result and DUP not in world['saved']

    @pytest.mark.parametrize('frames,message', [
        ({'start': 995, 'end': 1000, 'lead': 0, 'shots': [[1, 995, 1001]]}, 'Frames 995-1000 are outside the playback '
                                                                            'range [0, 1000).'),
        ({'start': 550, 'end': 560, 'lead': 0, 'shots': [[1, 550, 561]]}, f'Frame 550 is not inside a camera cut of '
                                                                          f'{SEQ}.'),
    ])
    def test_range_rules(self, tmp_path, monkeypatch, frames, message):
        result, error, world = run(tmp_path, monkeypatch, ue.graph(), piece_params(**frames))
        assert error is not None and result['error'] == message and not world['queues']


class TestSizeAndDraft:

    def test_a_resolution_on_a_graph_and_a_legacy_duplicate(self, tmp_path, monkeypatch):
        p = params(frame=100, render_settings=dict(SETTINGS, **{'unreal.resolution': [640, 360]}))
        result, error, world = run(tmp_path, monkeypatch, ue.graph(), p)
        _ok(result, error)
        props = _globals(world)
        assert props['override_output_resolution'] and (props['output_resolution'].resolution.x,
                                                        props['output_resolution'].resolution.y) == (640, 360)
        result, error, world = run(tmp_path, monkeypatch, legacy(), p)
        _ok(result, error)
        out = world['assets'][DUP].settings[0]
        assert (out.props['output_resolution'].x, out.props['output_resolution'].y) == (640, 360)

    def _graph_samples(self):
        temporal = ue.MovieGraphSamplingMethodNode(override_temporal_sample_count=True, temporal_sample_count=8)
        other = ue.MovieGraphSamplingMethodNode(override_temporal_sample_count=False, temporal_sample_count=8)
        spatial = ue.MovieGraphDeferredRenderPassNode(override_spatial_sample_count=True, spatial_sample_count=2)
        return ue.graph(passes=0, extra_outputs=[spatial], overrides=[temporal, other, ue.MovieGraphCameraSettingNode()])

    def test_graph_draft_only_lowers_samples(self, tmp_path, monkeypatch):
        draft = {'temporal_samples': 3, 'spatial_samples': 4}
        result, error, world = run(tmp_path, monkeypatch, self._graph_samples(), piece_params(draft=draft))
        _ok(result, error)
        dup = world['assets'][DUP]
        t_dup, o_dup, cam = dup.branches['Globals'][1:4]
        assert (t_dup.props['temporal_sample_count'], o_dup.props['temporal_sample_count']) == (3, 8)
        assert dup.branches['DefaultLayer'][-1].props['spatial_sample_count'] == 2  # never raised above the preset
        assert cam.props == ue.MovieGraphCameraSettingNode().props and 'motion_blur_off' not in result

    @pytest.mark.parametrize('kind', ['graph', 'legacy'])
    def test_motion_blur_off_forces_one_temporal_sample_and_leaves_cameras_alone(self, tmp_path, monkeypatch, kind):
        """Owner decision (a): temporal samples 1 here, r.MotionBlurQuality=0 from the worker; spatial as set."""
        aa = ue.MoviePipelineAntiAliasingSetting(temporal_sample_count=8, spatial_sample_count=4)
        cam = ue.MoviePipelineCameraSetting()
        preset = self._graph_samples() if kind == 'graph' else legacy(aa, cam)
        draft = {'temporal_samples': 3, 'motion_blur': False}
        result, error, world = run(tmp_path, monkeypatch, preset, piece_params(draft=draft))
        _ok(result, error)
        assert result['motion_blur_off'] == 'samples1_pp_off' and result['warnings'] == []
        dup = world['assets'][DUP]
        if kind == 'graph':
            t_dup, o_dup, cam_dup = dup.branches['Globals'][1:4]
            assert (t_dup.props['temporal_sample_count'], o_dup.props['temporal_sample_count']) == (1, 8)
            assert dup.branches['DefaultLayer'][-1].props['spatial_sample_count'] == 2
        else:
            aa_dup, cam_dup = dup.settings[3], dup.settings[4]
            assert (aa_dup.props['temporal_sample_count'], aa_dup.props['spatial_sample_count']) == (1, 4)
        assert cam_dup.props == type(cam_dup)().props  # the artist's camera settings are never touched

    def test_legacy_draft_exr_and_d5b(self, tmp_path, monkeypatch):
        aa = ue.MoviePipelineAntiAliasingSetting(temporal_sample_count=8, spatial_sample_count=1)
        draft = {'temporal_samples': 2, 'spatial_samples': 1, 'motion_blur': False}
        result, error, world = run(tmp_path, monkeypatch, legacy(aa), piece_params(fmt='EXR', draft=draft))
        _ok(result, error)
        settings = world['assets'][DUP].settings
        out = settings[0].props
        assert (out['handle_frame_count'], out['frame_number_offset'], out['output_frame_step'],
                out['zero_pad_frame_numbers']) == (0, 0, 1, 4)
        assert (out['custom_start_frame'], out['custom_end_frame'], out['use_custom_playback_range']) == (70, 130, True)
        assert out['file_name_format'] == 'sethlans_42.{frame_number}'
        exr = [s for s in settings if isinstance(s, ue.MoviePipelineImageSequenceOutput_EXR)]
        png = [s for s in settings if isinstance(s, ue.MoviePipelineImageSequenceOutput_PNG)]
        assert len(exr) == 1 and exr[0].is_enabled() and not png[0].is_enabled()
        assert settings[3].props['temporal_sample_count'] == 1  # motion blur off: one temporal sample
        assert not [s for s in settings if isinstance(s, ue.MoviePipelineCameraSetting)]  # no camera setting added


class TestDraftConflicts:

    def test_graph_set_console_variable_nodes(self, tmp_path, monkeypatch):
        """UE 5.8.4's graph names a CVar only on a Set Console Variable node (``MovieGraphSetCVarValueNode``, #283)."""
        nodes = [ue.MovieGraphSetCVarValueNode(override_name=True, name='r.screenpercentage', override_value=True),
                 ue.MovieGraphSetCVarValueNode(override_name=True, name='r.Other.Thing'),
                 ue.MovieGraphSetCVarValueNode(override_name=True, name='r.RayTracing.Shadows', disabled=True),
                 ue.MovieGraphSetCVarValueNode(override_name=False, name='r.RayTracing.Shadows')]
        draft = {'screen_percentage': 50, 'ray_traced_shadows': False}
        result, error, _world = run(tmp_path, monkeypatch, ue.graph(overrides=nodes), piece_params(draft=draft))
        _ok(result, error)
        assert result['draft_conflicts'] == ['r.ScreenPercentage'] and result['warnings'] == []

    def test_a_graph_cvar_preset_warns(self, tmp_path, monkeypatch):
        node = ue.MovieGraphApplyCVarPresetNode(override_console_variable_preset=True, console_variable_preset='p')
        draft = {'screen_percentage': 50}
        result, error, _world = run(tmp_path, monkeypatch, ue.graph(overrides=[node]), piece_params(draft=draft))
        _ok(result, error)
        assert result['draft_conflicts'] == [] and result['warnings'] == [
            "The preset uses console variable presets; a draft value they set is not detected."]

    def test_legacy_setting_and_an_unreadable_one(self, tmp_path, monkeypatch):
        draft = {'ray_traced_reflections': False}
        cv = ue.MoviePipelineConsoleVariableSetting(entries=[('r.Lumen.Reflections.HardwareRayTracing', 1.0, True),
                                                             ('r.ScreenPercentage', 50.0, False)])
        result, error, _world = run(tmp_path, monkeypatch, legacy(cv), piece_params(draft=draft))
        _ok(result, error)
        assert result['draft_conflicts'] == ['r.Lumen.Reflections.HardwareRayTracing']
        broken = ue.MoviePipelineConsoleVariableSetting()
        del broken.props['console_variable_presets']  # no silent fallback (#283): a missing property fails the prep
        result, error, _world = run(tmp_path, monkeypatch, legacy(broken), piece_params(draft=draft))
        assert error is not None and result['error'] == ("Unreal has no property console_variable_presets on "
                                                         "MoviePipelineConsoleVariableSetting; Sethlans needs UE "
                                                         "5.8.4's property names.")


class MultiLayerExr(ue.MovieGraphImageSequenceOutputNode_EXR):
    """UE's multilayer EXR node derives from the EXR node (review B2; the by-hand run confirms the class tree)."""


class TestOneExrNode:

    def test_the_multilayer_subclass_is_never_kept(self, tmp_path, monkeypatch):
        multi, exr = MultiLayerExr(), ue.MovieGraphImageSequenceOutputNode_EXR(disabled=True)
        result, error, world = run(tmp_path, monkeypatch, ue.graph(extra_outputs=[multi, exr]), piece_params(fmt='EXR'))
        _ok(result, error)
        png_dup, multi_dup, exr_dup = world['assets'][DUP].branches['DefaultLayer'][-3:]
        assert png_dup.is_disabled() and multi_dup.is_disabled() and not exr_dup.is_disabled()
        assert 'file_name_format' not in multi_dup.props

    def test_an_enabled_exr_node_is_preferred(self, tmp_path, monkeypatch):
        off, on = ue.MovieGraphImageSequenceOutputNode_EXR(disabled=True), ue.MovieGraphImageSequenceOutputNode_EXR()
        result, error, world = run(tmp_path, monkeypatch, ue.graph(extra_outputs=[off, on]), piece_params(fmt='EXR'))
        _ok(result, error)
        off_dup, on_dup = world['assets'][DUP].branches['DefaultLayer'][-2:]
        assert off_dup.is_disabled() and not on_dup.is_disabled() and on_dup.props['file_name_format']

    @pytest.mark.parametrize('disabled,n', [(False, 2), (True, 2)])
    def test_two_exr_nodes_that_could_render_fail(self, tmp_path, monkeypatch, disabled, n):
        nodes = [ue.MovieGraphImageSequenceOutputNode_EXR(disabled=disabled) for _ in range(2)]
        result, error, world = run(tmp_path, monkeypatch, ue.graph(extra_outputs=nodes), piece_params(fmt='EXR'))
        assert error is not None and not world['saved'] and 'queue_asset' not in result
        assert result['error'] == f"The preset has {n} EXR output nodes that could render; keep exactly one enabled to " \
                                  "render EXR."

    def test_only_a_multilayer_node_is_no_exr_node(self, tmp_path, monkeypatch):
        result, error, _world = run(tmp_path, monkeypatch, ue.graph(extra_outputs=[MultiLayerExr()]),
                                    piece_params(fmt='EXR'))
        assert error is not None and result['error'] == "The preset has no EXR output node; add one to render EXR."
        listed, error, _world = run_list(tmp_path, monkeypatch, ue.graph(extra_outputs=[MultiLayerExr()]),
                                         ue.Sequence(0, 1000, [CUTS]))
        assert error is None and listed['preset_facts']['can_exr'] is False  # discovery agrees (FR-SQ-6)


class TestZeroPadMotionBlurAndAuthor:

    @pytest.mark.parametrize('pad,kept', [(2, 4), (6, 6), (9, 7)])
    def test_the_zero_pad_stays_within_4_to_7(self, tmp_path, monkeypatch, pad, kept):
        preset = ue.graph()
        preset.branches['Globals'][0].props.update(override_zero_pad_frame_numbers=True, zero_pad_frame_numbers=pad)
        result, error, world = run(tmp_path, monkeypatch, preset, piece_params())
        _ok(result, error)
        assert _globals(world)['zero_pad_frame_numbers'] == kept
        legacy_preset = legacy()
        legacy_preset.settings[0].props['zero_pad_frame_numbers'] = pad
        result, error, world = run(tmp_path, monkeypatch, legacy_preset, piece_params())
        _ok(result, error)
        assert world['assets'][DUP].settings[0].props['zero_pad_frame_numbers'] == kept

    def test_the_motion_blur_path_is_reported_only_when_asked(self, tmp_path, monkeypatch):
        result, error, _world = run(tmp_path, monkeypatch, legacy(), piece_params(draft={'motion_blur': False}))
        _ok(result, error)
        assert result['motion_blur_off'] == 'samples1_pp_off'
        result, error, _world = run(tmp_path, monkeypatch, ue.graph(), piece_params(draft={'temporal_samples': 1}))
        _ok(result, error)
        assert 'motion_blur_off' not in result

    def test_every_queue_job_has_a_fixed_author(self, tmp_path, monkeypatch):
        """EXR metadata ``unreal/jobAuthor`` carries the job's author: never the OS user name."""
        p = params(kind='pilot', render_settings=dict(SETTINGS, **{'unreal.pilot_frames': [0, 90]}))
        for preset, job in ((ue.graph(), piece_params()), (legacy(), p)):
            result, error, world = run(tmp_path, monkeypatch, preset, job)
            _ok(result, error)
            (_name, queue), = world['queues']
            assert [j.author for j in queue.jobs] == ['Sethlans'] * len(queue.jobs)


class TestSequencePilot:

    def test_the_managers_frames_each_with_its_own_duplicate_and_d5b(self, tmp_path, monkeypatch):
        p = params(kind='pilot', render_settings=dict(SETTINGS, **{'unreal.pilot_frames': [0, 90, 700]}))
        result, error, world = run(tmp_path, monkeypatch, ue.graph(), p)
        _ok(result, error)
        assert result['pilot_frames'] == [0, 90, 700] and result['queue_jobs'] == 3
        assert _globals(world)['zero_pad_frame_numbers'] == 4 and _globals(world)['handle_frame_count'] == 0

    def test_a_pilot_frame_in_a_gap_fails(self, tmp_path, monkeypatch):
        p = params(kind='pilot', render_settings=dict(SETTINGS, **{'unreal.pilot_frames': [0, 550]}))
        result, error, _world = run(tmp_path, monkeypatch, ue.graph(), p)
        assert error is not None and result['error'] == f'Pilot frame 550 is not inside a camera cut of {SEQ}.'
