# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The prep script's ``prepare_audio`` mode and the audio edits module against the stub ``unreal`` module (sequence
rendering FR-SA-2, D5b, D7, U-4). The params are the ones the worker writes (``sequence_prep.audio_params``):
image outputs off, the audio output kept with the farm's file name, 128x72, 2 temporal samples, D5b, the range from
``r0``; the artist's preset is never touched.
"""
import ast
import json
import runpy
import sys

import pytest

from sethlans_worker_agent.unreal_executor import job_dir, sequence_prep
from tests.unit.worker.unreal import _ue_stub as ue
from tests.unit.worker.unreal.sequence._pieces import audio_params
from tests.unit.worker.unreal.ue_scripts.test_range_prep import CUTS, DUP, PRESET, SEQ, legacy

SCRIPT = str(job_dir.SCRIPTS_SOURCE / job_dir.PREP_SCRIPT)
AUDIO_PATH = job_dir.SCRIPTS_SOURCE / job_dir.AUDIO_EDITS_SCRIPT


def run(tmp_path, monkeypatch, preset, scripts_dir=None):
    world = {'assets': {PRESET: preset, SEQ: ue.Sequence(0, 1000, [CUTS], rate=(30000, 1001))}}
    monkeypatch.setitem(sys.modules, 'unreal', ue.make_unreal(world))
    data = sequence_prep.audio_params(audio_params(), 'D:\\c\\j\\42-x\\output', str(tmp_path / 'result.json'),
                                      scripts_dir or str(job_dir.SCRIPTS_SOURCE))
    (tmp_path / 'params.json').write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setenv('SETHLANS_UE_JOB_PARAMS', str(tmp_path / 'params.json'))
    error = None
    try:
        runpy.run_path(SCRIPT, run_name='sethlans_prep')
    except Exception as exc:  # the script re-raises whatever failed it
        error = exc
    return json.loads((tmp_path / 'result.json').read_text(encoding='utf-8')), error, world


def _sampling(**props):
    return ue.MovieGraphSamplingMethodNode(**{'override_temporal_sample_count': True, 'temporal_sample_count': 8,
                                              **props})


def audio_graph(audio=(None,), sampling=(None,), exr=False):
    outputs = [ue.MovieGraphAudioOutputNode(disabled=bool(d)) for d in audio]
    if exr:
        outputs.append(ue.MovieGraphImageSequenceOutputNode_EXR())
    return ue.graph(extra_outputs=outputs, overrides=[_sampling(disabled=bool(d)) for d in sampling])


class TestGraph:

    def test_the_audio_only_duplicate(self, tmp_path, monkeypatch):
        preset = audio_graph(exr=True)
        result, error, world = run(tmp_path, monkeypatch, preset)
        assert error is None and result['ok'] is True, result
        assert result['frame_rate'] == [30000, 1001] and result['queue_jobs'] == 1
        dup = world['assets'][DUP]
        png, audio, exr = dup.branches['DefaultLayer'][-3:]
        assert png.is_disabled() and exr.is_disabled() and not audio.is_disabled()
        assert (audio.props['override_file_name_format'], audio.props['file_name_format']) == \
            (True, 'sethlans_42.{frame_number}')
        assert not dup.branches['DefaultLayer'][1].is_disabled()  # the render pass is kept: MRG needs image data
        globals_, sampling = dup.branches['Globals'][:2]
        assert (globals_.props['custom_playback_range_start'].value, globals_.props['custom_playback_range_end'].value) \
            == (92, 280)
        size = globals_.props['output_resolution'].resolution
        assert globals_.props['override_output_resolution'] and (size.x, size.y) == (128, 72)
        assert (globals_.props['handle_frame_count'], globals_.props['zero_pad_frame_numbers']) == (0, 4)
        assert (sampling.props['override_temporal_sample_count'], sampling.props['temporal_sample_count']) == (True, 2)
        assert preset.branches['Globals'][1].props['temporal_sample_count'] == 8  # the artist's preset is untouched
        assert world['saved'][0] is dup and preset not in world['saved']

    def test_a_disabled_audio_node_is_enabled_on_the_copy(self, tmp_path, monkeypatch):
        preset = audio_graph(audio=(True,))
        result, error, world = run(tmp_path, monkeypatch, preset)
        assert error is None and not world['assets'][DUP].branches['DefaultLayer'][-1].is_disabled()
        assert preset.branches['DefaultLayer'][-1].is_disabled()

    @pytest.mark.parametrize('preset,message', [
        (lambda: ue.graph(overrides=[_sampling()]), "The preset has no audio output node; add one to render sound."),
        (lambda: audio_graph(audio=(None, None)), "The preset has 2 audio output nodes that could render; keep "
                                                  "exactly one enabled to render sound."),
        (lambda: audio_graph(sampling=()), "The preset has no enabled sampling node to set 2 temporal samples; "
                                           "add one to render sound."),
        (lambda: audio_graph(sampling=(True,)), "The preset has no enabled sampling node to set 2 temporal samples; "
                                                "add one to render sound."),
    ], ids=['no-audio-node', 'two-audio-nodes', 'no-sampling-node', 'disabled-sampling-node'])
    def test_presets_the_audio_job_cannot_use(self, tmp_path, monkeypatch, preset, message):
        result, error, world = run(tmp_path, monkeypatch, preset())
        assert error is not None and result['error'] == message and DUP not in world['saved']


SETS = ("The preset sets {name}, which changes how sounds end and repeat; the sound job cannot see the preset's "
        "value. Remove it from the preset to render sound.")


class TestSoundCvars:
    """#283 review: the export reads these CVars before the render; a preset that sets them itself is refused."""

    @pytest.mark.parametrize('name', ['Sequencer.Audio.UsePlayUntilFinished', 'sequencer.audio.userepeating'])
    def test_a_set_console_variable_node_refuses_the_audio_job(self, tmp_path, monkeypatch, name):
        node = ue.MovieGraphSetCVarValueNode(override_name=True, name=name, override_value=True, value=0.0)
        preset = audio_graph()
        preset.branches['Globals'].append(node)
        result, error, world = run(tmp_path, monkeypatch, preset)
        wanted = 'Sequencer.Audio.UsePlayUntilFinished' if 'Finished' in name else 'Sequencer.Audio.UseRepeating'
        assert error is not None and result['error'] == SETS.format(name=wanted) and DUP not in world['saved']

    def test_a_legacy_console_variable_refuses_and_a_disabled_one_does_not(self, tmp_path, monkeypatch):
        cv = ue.MoviePipelineConsoleVariableSetting(entries=[('Sequencer.Audio.UseRepeating', 0.0, True)])
        result, error, _world = run(tmp_path, monkeypatch, legacy(cv))
        assert error is not None and result['error'] == SETS.format(name='Sequencer.Audio.UseRepeating')
        off = ue.MoviePipelineConsoleVariableSetting(entries=[('Sequencer.Audio.UseRepeating', 0.0, False)])
        result, error, _world = run(tmp_path, monkeypatch, legacy(off))
        assert error is None and result['ok'] is True, result


class TestLegacy:

    def test_the_wave_output_is_added_and_every_image_output_disabled(self, tmp_path, monkeypatch):
        preset = legacy(ue.MoviePipelineImageSequenceOutput_EXR())
        result, error, world = run(tmp_path, monkeypatch, preset)
        assert error is None and result['ok'] is True, result
        settings = world['assets'][DUP].settings
        waves = [s for s in settings if isinstance(s, ue.MoviePipelineWaveOutput)]
        images = [s for s in settings if isinstance(s, (ue.MoviePipelineImageSequenceOutput_PNG,
                                                        ue.MoviePipelineImageSequenceOutput_EXR))]
        assert len(waves) == 1 and waves[0].is_enabled() and waves[0].props['file_name_format_override'] == ''
        assert images and not any(s.is_enabled() for s in images)
        out = settings[0].props
        assert (out['output_resolution'].x, out['output_resolution'].y) == (128, 72)
        assert (out['custom_start_frame'], out['custom_end_frame'], out['file_name_format']) == \
            (92, 280, 'sethlans_42.{frame_number}')
        assert (out['handle_frame_count'], out['frame_number_offset'], out['output_frame_step']) == (0, 0, 1)
        aa, = [s for s in settings if isinstance(s, ue.MoviePipelineAntiAliasingSetting)]
        assert aa.is_enabled() and aa.props['temporal_sample_count'] == 2  # added with the class defaults
        assert not [s for s in preset.settings if isinstance(s, ue.MoviePipelineWaveOutput)]

    def test_an_existing_wave_output_and_anti_aliasing_setting_are_reused(self, tmp_path, monkeypatch):
        wave = ue.MoviePipelineWaveOutput(file_name_format_override='{shot_name}', disabled=True)
        aa = ue.MoviePipelineAntiAliasingSetting(temporal_sample_count=15, spatial_sample_count=4)
        result, error, world = run(tmp_path, monkeypatch, legacy(wave, aa))
        assert error is None, result
        settings = world['assets'][DUP].settings
        wave_dup, aa_dup = settings[3], settings[4]
        assert wave_dup.is_enabled() and wave_dup.props['file_name_format_override'] == ''
        assert (aa_dup.props['temporal_sample_count'], aa_dup.props['spatial_sample_count']) == (2, 4)
        assert len(settings) == 5


class TestModule:

    def test_it_imports_only_unreal_and_never_saves(self):
        text = AUDIO_PATH.read_text(encoding='utf-8')
        tree = ast.parse(text)
        imported = [a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names]
        assert imported == ['unreal']
        assert not {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} & {'sys', '__import__', 'exec', 'open'}
        for call in ('save_loaded_asset', 'duplicate_asset', 'save_asset'):
            assert call not in text, call

    def test_the_prep_loads_it_by_file_path_in_audio_mode_only(self, tmp_path, monkeypatch):
        text = (job_dir.SCRIPTS_SOURCE / job_dir.PREP_SCRIPT).read_text(encoding='utf-8')
        assert 'AUDIO = "sethlans_audio_edits"' in text and 'load_required(AUDIO, "audio edits", "AudioEditsError")' in text
        scripts = tmp_path / 'scripts'
        scripts.mkdir()
        for name in (job_dir.FACTS_SCRIPT, job_dir.SHOTS_SCRIPT, job_dir.EDITS_SCRIPT):
            (scripts / name).write_bytes((job_dir.SCRIPTS_SOURCE / name).read_bytes())
        result, error, _world = run(tmp_path, monkeypatch, audio_graph(), scripts_dir=str(scripts))
        assert error is not None and result['error'] == "The audio edits module did not load (FileNotFoundError)."
        assert str(tmp_path) not in json.dumps(result)
