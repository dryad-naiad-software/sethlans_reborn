# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Issue #283's audit: the UE-side scripts no longer fall back silently when an Unreal name is missing. Each case
below used to carry on with a guess (a section counted as active, a zero start offset, an empty shot name, the
deprecated ``set_current_time``, the playback sample rate, a sound of unknown length marked unsupported, no bound
sections, a wave output left as it was); now the step fails, naming the missing call where Python gives its name.

``hiding`` takes one call away from a stub object, as an Unreal class without it would; no name is added.
"""
import ast
import types

import pytest

from sethlans_worker_agent.unreal_executor import job_dir
from tests.unit.worker.unreal import _ue_stub as ue
from tests.unit.worker.unreal.ue_scripts import test_audio_export as export, test_audio_prep as audio
from tests.unit.worker.unreal.ue_scripts._discovery import shots_of
from tests.unit.worker.unreal.ue_scripts.test_range_prep import legacy

PREP_FAILED = "Unreal prep pass failed (AttributeError: no {name})."
EXPORT_FAILED = "The sound export failed (AttributeError: no {name})."
PROP_MISSING = "Unreal has no property {name} on {cls}; the sound sources were not read."  # named (#283 review)
UNTIL = {'Sequencer.Audio.UsePlayUntilFinished': '1'}


def hiding(obj, name):
    """``obj``, now of a subclass whose objects lack ``name`` (an ``AttributeError`` naming it, as Python raises)."""
    def __getattribute__(self, attr):
        if attr == name:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{attr}'", name=attr, obj=self)
        return object.__getattribute__(self, attr)
    obj.__class__ = type(type(obj).__name__, (type(obj),), {'__getattribute__': __getattribute__})
    return obj


class TestShots:
    """``sethlans_shots``: ``active``, ``_mapped`` and ``_name_of`` read UE 5.8.4's names, never a default."""

    def _error(self, tmp_path, monkeypatch, section):
        return shots_of(tmp_path, monkeypatch, [ue.MovieSceneCinematicShotTrack([section])])

    def test_a_section_without_is_active_fails_the_discovery(self, tmp_path, monkeypatch):
        section = hiding(ue.shot(0, 100, [(0, 100)], name='sh010'), 'is_active')
        assert self._error(tmp_path, monkeypatch, section) == PREP_FAILED.format(name='is_active')

    def test_a_camera_cut_without_is_active_fails_the_discovery(self, tmp_path, monkeypatch):
        cuts = ue.MovieSceneCameraCutTrack([hiding(ue.Section(0, 100), 'is_active')])
        assert shots_of(tmp_path, monkeypatch, [cuts]) == PREP_FAILED.format(name='is_active')

    def test_parameters_without_a_start_offset_fail_instead_of_offset_0(self, tmp_path, monkeypatch):
        section = ue.shot(0, 100, [(0, 100)], name='sh010')
        section.parameters = ue.Struct(time_scale=ue.TimeWarp())
        assert self._error(tmp_path, monkeypatch, section) == PREP_FAILED.format(name='start_frame_offset')

    def test_a_shot_sequence_without_get_name_fails_instead_of_no_name(self, tmp_path, monkeypatch):
        section = ue.shot(0, 100, [(0, 100)], name='sh010')
        hiding(section.sequence, 'get_name')
        assert self._error(tmp_path, monkeypatch, section) == PREP_FAILED.format(name='get_name')


def _set_time(unreal):
    """``set_time`` from the warm-up script's source (loading the script runs an editor session)."""
    script = job_dir.SCRIPTS_SOURCE / job_dir.WARMUP_SCRIPT
    func, = [n for n in ast.parse(script.read_text(encoding='utf-8')).body
             if isinstance(n, ast.FunctionDef) and n.name == 'set_time']
    namespace = {'unreal': unreal}
    exec(compile(ast.Module(body=[func], type_ignores=[]), str(script), 'exec'), namespace)  # noqa: S102 - our file
    return namespace['set_time']


def _warm_unreal(library):
    """The names ``set_time`` reads, all on the verified list; ``library`` is the Sequencer editor library."""
    return types.SimpleNamespace(
        MovieSceneSequencePlaybackParams=types.SimpleNamespace, FrameTime=lambda n: ('time', n),
        FrameNumber=lambda f: ('frame', f), MovieScenePositionType=types.SimpleNamespace(FRAME='FRAME'),
        UpdatePositionMethod=types.SimpleNamespace(SCRUB='SCRUB'),
        MovieSceneTimeUnit=types.SimpleNamespace(DISPLAY_RATE='DISPLAY_RATE'), LevelSequenceEditorBlueprintLibrary=library)


class TestWarmUpTime:

    def test_time_is_set_with_set_global_position(self):
        calls = []
        library = types.SimpleNamespace(set_global_position=lambda params, unit: calls.append((params, unit)))
        _set_time(_warm_unreal(library))(120)
        (params, unit), = calls
        assert (params.frame, params.position_type, params.update_method, unit) == \
            (('time', ('frame', 120)), 'FRAME', 'SCRUB', 'DISPLAY_RATE')

    def test_without_set_global_position_it_fails_naming_it(self):
        """The deprecated ``set_current_time`` is never tried: the error names ``set_global_position``."""
        with pytest.raises(AttributeError) as caught:
            _set_time(_warm_unreal(types.SimpleNamespace()))(120)
        assert caught.value.name == 'set_global_position'


class NoImportedRate(ue.Sound):
    """A wave whose ``imported_sample_rate`` cannot be read (its playback ``sample_rate`` can)."""

    def get_editor_property(self, name):
        if name == 'imported_sample_rate':
            raise AttributeError(f"no property {name}")
        return super().get_editor_property(name)


class TestAudioExport:

    def _fails(self, tmp_path, monkeypatch, seq, world=None):
        result, error, _world = export.run(tmp_path, monkeypatch, seq, world=world or {})
        assert error is not None and result['ok'] is False and result['sections'] == [], result
        return result['error']

    def test_an_unreadable_imported_rate_fails_instead_of_the_playback_rate(self, tmp_path, monkeypatch):
        wave = NoImportedRate('/Game/A/Music.Music')
        seq = export._sequence(ue.AudioSection(100, 200, wave))
        assert self._fails(tmp_path, monkeypatch, seq) == PROP_MISSING.format(name='imported_sample_rate',
                                                                              cls='NoImportedRate')

    def test_a_play_until_finished_wave_of_unknown_length_fails(self, tmp_path, monkeypatch):
        """It used to be an ``unsupported`` row; with the CVar on its length is needed, and a missing one fails."""
        seq = export._sequence(ue.AudioSection(100, 150, ue.Sound('/Game/A/Odd.Odd'), until=True))
        assert self._fails(tmp_path, monkeypatch, seq, {'cvars': UNTIL}) == PROP_MISSING.format(name='duration',
                                                                                                cls='Sound')

    def test_a_section_without_get_play_until_finished_fails_naming_it(self, tmp_path, monkeypatch):
        """Read only with ``Sequencer.Audio.UsePlayUntilFinished`` on; off, the When Finished mode decides."""
        section = hiding(ue.AudioSection(100, 150, export.MUSIC), 'get_play_until_finished')
        assert self._fails(tmp_path, monkeypatch, export._sequence(section), {'cvars': UNTIL}) == \
            EXPORT_FAILED.format(name='get_play_until_finished')

    def test_a_section_without_get_completion_mode_fails_naming_it(self, tmp_path, monkeypatch):
        section = hiding(ue.AudioSection(100, 150, export.MUSIC), 'get_completion_mode')
        assert self._fails(tmp_path, monkeypatch, export._sequence(section)) == \
            EXPORT_FAILED.format(name='get_completion_mode')

    def test_a_sequence_without_get_bindings_fails_naming_it(self, tmp_path, monkeypatch):
        seq = hiding(export._sequence(ue.AudioSection(100, 200, export.MUSIC)), 'get_bindings')
        assert self._fails(tmp_path, monkeypatch, seq) == EXPORT_FAILED.format(name='get_bindings')


def test_a_legacy_wave_output_without_its_file_name_override_fails_the_audio_prep(tmp_path, monkeypatch):
    """``sethlans_audio_edits._legacy``: the override used to be skipped when unreadable; now the prep fails."""
    wave = ue.MoviePipelineWaveOutput()
    del wave.props['file_name_format_override']
    result, error, world = audio.run(tmp_path, monkeypatch, legacy(wave))
    assert error is not None and result['error'] == ("Unreal has no property file_name_format_override on "
                                                     "MoviePipelineWaveOutput; Sethlans needs UE 5.8.4's property names.")
    assert not world['saved']
