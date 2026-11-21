# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
How long and how often a sound plays in UE 5.8.4, as the sound-source export reports it (#283 domain review).

* A one-shot, non-repeating section plays on past its end when its When Finished mode is Keep State (directly, or
  Project Default with the sequence's default Keep State): ``Sequencer.Audio.UsePlayUntilFinished`` is off by
  default and then the completion mode decides (MovieSceneAudioSystem.cpp:441-445). An unknown mode is unsupported.
* A section loops on ``repeating`` with ``Sequencer.Audio.UseRepeating`` on (its default), on the legacy
  ``looping`` with it off; a wave that is not one-shot always loops, and is never extended (cpp:415-433, 464).

The stub's CVars start at UE's defaults (``CVAR_DEFAULTS``). A 5 s wave from frame 100 at 24 fps after a 1 s start
offset ends at 196.
"""
import pytest

from tests.unit.worker.unreal import _ue_stub as ue
from tests.unit.worker.unreal.ue_scripts.test_audio_export import _sequence, run

LONG = ue.Sound('/Game/A/Long.Long', duration=5.0)
LOOP_WAVE = ue.Sound('/Game/A/Loop.Loop', duration=5.0, looping=True)
EXPORTS = {LONG.path: b'RIFF', LOOP_WAVE.path: b'RIFF'}


def project_ini(tmp_path, body):
    """A project ``Config/DefaultEngine.ini`` holding ``body``; its folder for ``world['project_config']``."""
    folder = tmp_path / 'project_config'
    folder.mkdir(exist_ok=True)
    (folder / 'DefaultEngine.ini').write_text(body, encoding='utf-8')
    return str(folder)


def rows(tmp_path, monkeypatch, *sections, cvars=None, completion=None, result_of=False, cls='LevelSequence'):
    """``completion``: the project's level sequence default (``None``: the engine's RestoreState applies)."""
    world = {'exports': EXPORTS, 'cvars': cvars or {}}
    if completion is not None:
        mode = completion.title().replace('_', '')  # KEEP_STATE -> KeepState
        body = f"[/Script/LevelSequence.LevelSequence]\nDefaultCompletionMode={mode}\n"
        world['project_config'] = project_ini(tmp_path, body)
    seq = _sequence(*sections)
    seq.cls = cls  # the owning sequence's class
    result, error, _world = run(tmp_path, monkeypatch, seq, world=world)
    assert error is None and result['ok'] is True, result
    return result if result_of else result['sections']


@pytest.mark.parametrize('section_mode,sequence_mode,end', [
    ('KEEP_STATE', 'RESTORE_STATE', 196), ('PROJECT_DEFAULT', 'KEEP_STATE', 196),
    ('RESTORE_STATE', 'KEEP_STATE', 150), ('PROJECT_DEFAULT', 'RESTORE_STATE', 150),
    ('PROJECT_DEFAULT', None, 150),  # no project override: the engine's BaseEngine.ini RestoreState
])
def test_keep_state_plays_the_wave_to_its_end_with_play_until_finished_off(tmp_path, monkeypatch, section_mode,
                                                                           sequence_mode, end):
    section = ue.AudioSection(100, 150, LONG, offset_ticks=24_000, completion=section_mode)
    row, = rows(tmp_path, monkeypatch, section, completion=sequence_mode)
    assert (row['end'], row['looping']) == (end, False)


def test_an_unknown_when_finished_mode_is_unsupported(tmp_path, monkeypatch):
    section = ue.AudioSection(100, 150, LONG, completion='SOMETHING_NEW')
    assert rows(tmp_path, monkeypatch, section) == [{'unsupported': 'an unknown When Finished mode'}]


def test_play_until_finished_on_ignores_the_when_finished_mode(tmp_path, monkeypatch):
    cvars = {'Sequencer.Audio.UsePlayUntilFinished': '1'}
    keep = ue.AudioSection(100, 150, LONG, offset_ticks=24_000, completion='KEEP_STATE')
    until = ue.AudioSection(100, 150, LONG, offset_ticks=24_000, until=True)
    assert [r['end'] for r in rows(tmp_path, monkeypatch, keep, until, cvars=cvars)] == [150, 196]


@pytest.mark.parametrize('use_repeating,repeating,looping,looped', [
    (None, True, False, True), (None, False, True, False),  # UseRepeating on (UE's default): repeating counts
    ('0', True, False, False), ('0', False, True, True),  # off: the legacy looping flag counts
])
def test_the_section_loop_flag_follows_use_repeating(tmp_path, monkeypatch, use_repeating, repeating, looping, looped):
    cvars = {} if use_repeating is None else {'Sequencer.Audio.UseRepeating': use_repeating}
    section = ue.AudioSection(100, 150, LONG, repeating=repeating, looping=looping)
    row, = rows(tmp_path, monkeypatch, section, cvars=cvars)
    assert (row['looping'], row['end']) == (looped, 150)


def test_a_wave_that_is_not_one_shot_always_loops_and_is_never_extended(tmp_path, monkeypatch):
    section = ue.AudioSection(100, 150, LOOP_WAVE, completion='KEEP_STATE')
    row, = rows(tmp_path, monkeypatch, section)
    assert (row['looping'], row['end']) == (True, 150)


def test_a_repeating_keep_state_section_is_not_extended(tmp_path, monkeypatch):
    """UE gives a looping or repeating section RestoreState (UpdateCompletionMode): it stops at its end."""
    section = ue.AudioSection(100, 150, LONG, repeating=True, completion='KEEP_STATE')
    row, = rows(tmp_path, monkeypatch, section)
    assert (row['looping'], row['end']) == (True, 150)


def _export_error(tmp_path, monkeypatch, world):
    world = dict(world, exports=EXPORTS)
    result, error, _world = run(tmp_path, monkeypatch, _sequence(ue.AudioSection(100, 150, LONG)), world=world)
    assert error is not None and result['ok'] is False and result['sections'] == []
    return result['error']


def test_no_config_setting_the_default_fails_loudly(tmp_path, monkeypatch):
    """``DefaultCompletionMode`` is config only, not a Python name: no value is assumed when no file sets it."""
    engine = tmp_path / 'engine'
    engine.mkdir()
    (engine / 'BaseEngine.ini').write_text('[/Script/TemplateSequence.TemplateSequence]\nDefaultCompletionMode=KeepState\n',
                                           encoding='utf-8')
    assert _export_error(tmp_path, monkeypatch, {'engine_config': str(engine)}) == (
        "Neither the project's DefaultEngine.ini nor the engine's BaseEngine.ini sets the level sequences' "
        "DefaultCompletionMode; the sound sources were not read.")


def test_an_unknown_default_fails_loudly(tmp_path, monkeypatch):
    project = project_ini(tmp_path, '[/Script/LevelSequence.LevelSequence]\nDefaultCompletionMode=ProjectDefault\n')
    assert _export_error(tmp_path, monkeypatch, {'project_config': project}) == (
        "The level sequences' DefaultCompletionMode in DefaultEngine.ini is not KeepState or RestoreState; the sound "
        "sources were not read.")


@pytest.mark.parametrize('body,value', [
    ('[/Script/LevelSequence.LevelSequence]\nDefaultCompletionMode=RestoreState\nDefaultCompletionMode=KeepState\n',
     'KeepState'),  # duplicate keys: the last one wins
    ('[/script/levelsequence.levelsequence]\n; DefaultCompletionMode=RestoreState\n+DefaultCompletionMode="KeepState"\n',
     'KeepState'),  # a comment, the + array syntax, quotes, the section's case
    ('[/Script/LevelSequence.LevelSequence]\nDefaultCompletionMode=KeepState\n-DefaultCompletionMode=KeepState\n', None),
    ('[/Script/LevelSequence.LevelSequence]\nOther=1\n[Next]\nDefaultCompletionMode=KeepState\n', None),
])
def test_the_config_reader(tmp_path, monkeypatch, body, value):
    rules = _rules_module(monkeypatch)
    (tmp_path / 'DefaultEngine.ini').write_text(body, encoding='utf-8')
    assert rules.ini_value(str(tmp_path / 'DefaultEngine.ini')) == value
    assert rules.ini_value(str(tmp_path / 'absent.ini')) is None


def _rules_module(monkeypatch):
    import importlib.util
    import sys

    from sethlans_worker_agent.unreal_executor import job_dir
    monkeypatch.setitem(sys.modules, 'unreal', ue.make_unreal({}))
    spec = importlib.util.spec_from_file_location('rules', job_dir.SCRIPTS_SOURCE / job_dir.SOUND_RULES_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ASSUMED = ("Sound sections with the project default When Finished mode were measured as {value}, the level sequence "
           "default in the config files; a saved sequence can hold its own value, which Sethlans cannot read.")


@pytest.mark.parametrize('completion,value,end', [('KEEP_STATE', 'KeepState', 196), (None, 'RestoreState', 150)])
def test_a_level_sequence_owner_uses_the_config_and_warns(tmp_path, monkeypatch, completion, value, end):
    sections = [ue.AudioSection(100, 150, LONG, offset_ticks=24_000), ue.AudioSection(100, 150, LONG, offset_ticks=24_000)]
    result = rows(tmp_path, monkeypatch, *sections, completion=completion, result_of=True)
    assert [r['end'] for r in result['sections']] == [end, end]
    assert result['warnings'].count(ASSUMED.format(value=value)) == 1  # once, however many sections


def test_no_warning_without_a_project_default_section(tmp_path, monkeypatch):
    result = rows(tmp_path, monkeypatch, ue.AudioSection(100, 150, LONG, completion='RESTORE_STATE'), result_of=True)
    assert not [w for w in result['warnings'] if 'project default' in w]


def test_another_sequence_class_owner_is_unsupported(tmp_path, monkeypatch):
    """DefaultCompletionMode is per class (TemplateSequence, plugin sequences set their own): not assumed."""
    section = ue.AudioSection(100, 150, LONG)
    assert rows(tmp_path, monkeypatch, section, completion='KEEP_STATE', cls='TemplateSequence') == [
        {'unsupported': 'a project default When Finished mode in a sequence that is not a LevelSequence'}]
    keep = ue.AudioSection(100, 150, LONG, offset_ticks=24_000, completion='KEEP_STATE')  # its own mode still counts
    (tmp_path / 'again').mkdir()
    assert rows(tmp_path / 'again', monkeypatch, keep, cls='TemplateSequence')[0]['end'] == 196


def test_a_shot_subsequence_owner_decides(tmp_path, monkeypatch):
    """UE reads the default from the sequence owning the section (MovieSceneCompiledDataManager.cpp:1462)."""
    inner = ue.MovieSceneAudioTrack([ue.AudioSection(100, 150, LONG)])
    shot = ue.shot(0, 1000, [(0, 20_000)], inner_tracks=[inner])
    shot.sequence.cls = 'MetaHumanSceneSequence'
    seq = _sequence(shots=[shot])
    result, error, _world = run(tmp_path, monkeypatch, seq, world={'exports': EXPORTS})
    assert error is None and result['sections'] == [
        {'unsupported': 'a project default When Finished mode in a sequence that is not a LevelSequence'}]
