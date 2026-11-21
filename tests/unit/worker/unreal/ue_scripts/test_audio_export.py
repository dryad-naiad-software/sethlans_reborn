# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The sound-source export commandlet (``ue_scripts/sethlans_audio_export.py``, sequence rendering D23, FR-SA-9)
against the stub ``unreal`` module, with the params the worker writes (``sequence_prep.export_params``): the rows
of the sections that overlap the run, start offsets in source samples, sounds inside shots mapped and clipped,
unsupported sections counted, each wave exported once, the limits, and errors without paths. The rows it writes
pass the worker's strict parse.
"""
import ast
import json
import runpy
import sys

import pytest

from sethlans_worker_agent.unreal_executor import job_dir, sequence_prep
from tests.unit.worker.unreal import _ue_stub as ue
from tests.unit.worker.unreal.sequence._pieces import audio_params

SCRIPT = job_dir.SCRIPTS_SOURCE / job_dir.EXPORT_SCRIPT
SEQ = '/Game/Seq/Edit'
MUSIC, CUE, DIALOG, FX44 = (ue.Sound('/Game/A/Music.Music'), ue.Sound('/Game/A/Cue.Cue', cls='SoundCue'),
                            ue.Sound('/Game/A/Dialog.Dialog'), ue.Sound('/Game/A/Fx.Fx', rate=44_100))
EXPORTS = {MUSIC.path: b'RIFF-music', DIALOG.path: b'RIFF-dialog', FX44.path: b'RIFF-fx'}


def _sequence(*sections, shots=(), bound=()):
    """24 fps, 24,000 ticks (1,000 ticks a frame); the run is frames 92-279 (``audio_params``)."""
    tracks = [ue.MovieSceneCameraCutTrack([ue.Section(0, 1000)]), ue.MovieSceneAudioTrack(list(sections))]
    if shots:
        tracks.append(ue.MovieSceneCinematicShotTrack(list(shots)))
    seq = ue.Sequence(0, 1000, tracks)
    if bound:
        seq.get_bindings = lambda: [ue.Binding([ue.MovieSceneAudioTrack(list(bound))])]
    return seq


def run(tmp_path, monkeypatch, seq, **params):
    world = {'assets': {SEQ: seq}, 'exports': dict(EXPORTS)}
    world.update(params.pop('world', {}))
    monkeypatch.setitem(sys.modules, 'unreal', ue.make_unreal(world))
    export_dir = tmp_path / 'audio_src'
    export_dir.mkdir()
    data = sequence_prep.export_params(audio_params(), str(export_dir), str(tmp_path / 'sources.json'),
                                       str(job_dir.SCRIPTS_SOURCE))
    data.update(params)
    (tmp_path / 'params.json').write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setenv('SETHLANS_UE_JOB_PARAMS', str(tmp_path / 'params.json'))
    error = None
    try:
        runpy.run_path(str(SCRIPT), run_name='sethlans_export')
    except Exception as exc:  # the script re-raises whatever failed it
        error = exc
    return json.loads((tmp_path / 'sources.json').read_text(encoding='utf-8')), error, world


def test_rows_for_the_sections_that_overlap_the_run(tmp_path, monkeypatch):
    seq = _sequence(ue.AudioSection(0, 500, MUSIC, offset_ticks=12_000, looping=True, volume=0.5),
                    ue.AudioSection(300, 400, MUSIC), ue.AudioSection(100, 200, CUE),
                    ue.AudioSection(120, 200, MUSIC, volume_keys=2), ue.AudioSection(150, 260, FX44, pitch=1.5),
                    ue.AudioSection(150, 260, FX44), ue.AudioSection(10, 50, MUSIC),
                    ue.AudioSection(100, 200, MUSIC, active=False))
    result, error, world = run(tmp_path, monkeypatch, seq)
    assert error is None and result['ok'] is True, result
    assert result['sections'] == [
        {'start': 0, 'end': 500, 'start_offset_samples': 24_000, 'volume': 0.5, 'looping': True, 'wav': 'src_000.wav',
         'sample_rate': 48_000},
        {'unsupported': 'a sound cue'}, {'unsupported': 'a keyed volume'}, {'unsupported': 'a pitch other than 1'},
        {'start': 150, 'end': 260, 'start_offset_samples': 0, 'volume': 1.0, 'looping': False, 'wav': 'src_001.wav',
         'sample_rate': 44_100}]
    assert [path for path, *_ in world['exported']] == [MUSIC.path, FX44.path]  # each wave once
    assert all(exporter == 'SoundExporterWAV' and automated and not prompt
               for _path, exporter, automated, prompt in world['exported'])
    assert (tmp_path / 'audio_src' / 'src_001.wav').read_bytes() == b'RIFF-fx'
    rows, unsupported = sequence_prep.parse_sources(result, 92, 280)
    assert len(rows) == 2 and unsupported == 3
    assert not any(isinstance(v, ue.Sound) for v in world['assets'].values())  # nothing was saved or added


def test_a_sound_inside_a_shot_is_mapped_and_clipped_to_the_shot(tmp_path, monkeypatch):
    """Inner frames 950-1300 of a shot at edit 200-300 whose sequence starts at 1000: edit 150-500, clipped to the
    shot; the 50 clipped frames (at 24 fps) move the start offset by 100,000 samples."""
    inner = ue.MovieSceneAudioTrack([ue.AudioSection(950, 1300, DIALOG, offset_ticks=24_000)])
    shot = ue.shot(200, 300, [(0, 20_000)], inner_start=1000, inner_tracks=[inner])
    result, error, _world = run(tmp_path, monkeypatch, _sequence(shots=[shot]))
    assert error is None, result
    assert result['sections'] == [{'start': 200, 'end': 300, 'start_offset_samples': 148_000, 'volume': 1.0,
                                   'looping': False, 'wav': 'src_000.wav', 'sample_rate': 48_000}]


def test_sounds_attached_to_objects_and_open_ranges_are_counted(tmp_path, monkeypatch):
    seq = _sequence(ue.AudioSection(0, 0, MUSIC, open_range=True), bound=[ue.AudioSection(0, 10, MUSIC)] * 2)
    result, error, _world = run(tmp_path, monkeypatch, seq)
    assert error is None
    assert result['sections'] == [{'unsupported': 'an open range'}] + [{'unsupported': 'a sound attached to an object'}] * 2
    assert sequence_prep.parse_sources(result, 92, 280) == ((), 3)


@pytest.mark.parametrize('change,message', [
    ({'max_sections': 1}, "The run has more than 1 audio sections; the sound offset is not measured."),
    ({'max_export_bytes': 5}, "The sound sources exceed the farm's export limit."),
    ({'world': {'export_fails': True}}, "A sound wave could not be exported."),
    ({'mode': 'prepare'}, "The sound export was started in the wrong mode."),
])
def test_limits_and_failures_have_fixed_text(tmp_path, monkeypatch, change, message):
    seq = _sequence(ue.AudioSection(0, 500, MUSIC), ue.AudioSection(100, 200, DIALOG))
    result, error, _world = run(tmp_path, monkeypatch, seq, **change)
    assert error is not None and result['ok'] is False and result['error'] == message and result['sections'] == []


def test_an_unexpected_error_is_reported_by_its_class_only(tmp_path, monkeypatch):
    broken = ue.AudioSection(0, 500, MUSIC)
    broken.get_all_channels = lambda: (_ for _ in ()).throw(RuntimeError(str(tmp_path / 'secret')))
    result, error, _world = run(tmp_path, monkeypatch, _sequence(broken))
    assert isinstance(error, RuntimeError) and result['error'] == "The sound export failed (RuntimeError)."
    assert str(tmp_path) not in json.dumps(result)


def test_it_reads_and_exports_only():
    text = SCRIPT.read_text(encoding='utf-8')
    tree = ast.parse(text)
    imported = sorted(a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names)
    assert imported == ['importlib.util', 'json', 'math', 'os', 'sys', 'time', 'unreal']
    for call in ('save_loaded_asset', 'set_editor_property', 'duplicate_asset', 'set_disabled'):
        assert call not in text, call
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == 'path'
                and isinstance(n.value, ast.Name) and n.value.id == 'sys']  # never sys.path
    assert 'importlib.util.spec_from_file_location(SHOTS, os.path.join(P["scripts_dir"], SHOTS + ".py"))' in text


def test_a_play_until_finished_wave_ends_where_it_ends(tmp_path, monkeypatch):
    """5.8: the sound outlasts its section; a 5 s wave from frame 100 (24 fps) after a 1 s start offset ends at 196."""
    long_wave = ue.Sound('/Game/A/Long.Long', duration=5.0)
    unknown = ue.Sound('/Game/A/Odd.Odd')
    seq = _sequence(ue.AudioSection(100, 150, long_wave, offset_ticks=24_000, until=True),
                    ue.AudioSection(100, 150, long_wave, until=True, looping=True),
                    ue.AudioSection(100, 150, unknown, until=True))
    result, error, _world = run(tmp_path, monkeypatch, seq, world={'exports': {long_wave.path: b'RIFF'}})
    assert error is None, result
    ends = [row.get('end') for row in result['sections']]
    assert ends == [196, 150, None]
    assert result['sections'][2] == {'unsupported': 'a play-until-finished sound of unknown length'}
