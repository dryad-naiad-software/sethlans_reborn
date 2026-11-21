# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The sound pieces of the stub ``unreal`` module (``_ue_stub``, which installs them): the legacy wave output, audio
tracks and sections with their volume and pitch channels, sound assets, and the asset export task that the export
commandlet (``ue_scripts/sethlans_audio_export.py``) runs with ``SoundExporterWAV``. ``world['exports']`` maps a
sound's path to the bytes its export writes; ``world['export_fails']`` makes every export return False.
"""
import types

from tests.unit.worker.unreal._ue_stub_mrq import MoviePipelineOutputBase, Struct, _Strict


class MoviePipelineWaveOutput(_Strict, MoviePipelineOutputBase):
    DEFAULTS = {'file_name_format_override': ''}


class Channel:
    """A scripting channel; ``default=None`` is one with no default, which UE 5.8.4's ``get_default()`` reads as 0."""

    def __init__(self, name, default=1.0, keys=0):
        self.name, self.default, self.keys = name, default, keys

    def get_name(self):
        return self.name

    def has_default(self):
        return self.default is not None

    def get_default(self):
        return 0.0 if self.default is None else self.default

    def get_keys(self):
        return [object()] * self.keys


class Sound:
    """A sound asset; ``cls`` is its UE class name (``SoundWave``, ``SoundCue``, ``MetaSoundSource``)."""

    def __init__(self, path, cls='SoundWave', rate=48_000, duration=None, looping=False):
        self.path, self.cls, self.rate, self.duration, self.looping = path, cls, rate, duration, looping

    def get_class(self):
        return types.SimpleNamespace(get_name=lambda: self.cls)

    def get_path_name(self):
        return self.path

    def get_editor_property(self, name):
        if name in ('imported_sample_rate', 'sample_rate'):
            return self.rate
        if name == 'duration' and self.duration is not None:
            return self.duration
        if name == 'looping':  # SoundWave.bLooping: a looping wave is not one-shot (USoundBase::IsOneShot)
            return self.looping
        raise AttributeError(name)


class AudioSection:
    def __init__(self, start, end, sound, offset_ticks=0, looping=False, volume=1.0, volume_keys=0, pitch=1.0,
                 pitch_keys=0, active=True, open_range=False, until=False, repeating=False, completion='PROJECT_DEFAULT'):
        self.start, self.end, self.active, self.open_range, self.until = start, end, active, open_range, until
        self.completion = completion
        self.props = {'sound': sound, 'looping': looping, 'repeating': repeating,
                      'start_frame_offset': Struct(value=offset_ticks)}
        self.channels = [Channel('Volume_1', volume, volume_keys), Channel('Pitch_1', pitch, pitch_keys),
                         Channel('AttachActor_1', None)]

    def get_editor_property(self, name):
        return self.props[name]

    def get_play_until_finished(self):
        return self.until

    def get_completion_mode(self):
        """``MovieSceneSection.GetCompletionMode``: a new audio section's is Project Default (cpp:148-152)."""
        return self.completion

    def get_all_channels(self):
        return self.channels

    def get_start_frame(self):
        return self.start

    def get_end_frame(self):
        return self.end

    def has_start_frame(self):
        return not self.open_range

    def has_end_frame(self):
        return not self.open_range

    def is_active(self):
        return self.active


class MovieSceneAudioTrack:
    def __init__(self, sections):
        self.sections = sections

    def get_sections(self):
        return self.sections


class Binding:
    def __init__(self, tracks):
        self.tracks = tracks

    def get_tracks(self):
        return self.tracks


def install(module, world):
    world.setdefault('exports', {})
    world.setdefault('exported', [])
    module.MoviePipelineWaveOutput = MoviePipelineWaveOutput
    module.MovieSceneAudioTrack = MovieSceneAudioTrack
    module.SoundExporterWAV = lambda: 'SoundExporterWAV'
    module.AssetExportTask = lambda: Struct(object=None, filename='', exporter=None, automated=False, prompt=True,
                                            replace_identical=False)

    def run_asset_export_task(task):
        if world.get('export_fails'):
            return False
        with open(task.filename, 'wb') as handle:
            handle.write(world['exports'].get(task.object.get_path_name(), b''))
        world['exported'].append((task.object.get_path_name(), task.exporter, task.automated, task.prompt))
        return True
    module.Exporter = types.SimpleNamespace(run_asset_export_task=run_asset_export_task)
    return module
