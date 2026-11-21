# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The output size on the farm's copy of a graph preset renders the size the job asked for (issue #283, D8, FR-SA-2).

UE 5.8.4 renders a Global Output Settings node's ``output_resolution`` by its profile name when Project Settings
knows that name, and only otherwise by the stored size (``GetDesiredOutputResolution``). A default-built named
resolution names ``1080p (FHD)``, so the edit must make a ``Custom`` one through ``unreal.MovieGraphLibrary`` (the
Python name of ``UMovieGraphBlueprintLibrary``) and check it held. The stub's ``resolved_size`` mirrors UE.
"""
import types

import pytest

from sethlans_worker_agent.unreal_executor import job_dir
from tests.unit.worker.unreal import _ue_stub as ue, _ue_stub_seq
from tests.unit.worker.unreal._executor_fakes import SETTINGS, params
from tests.unit.worker.unreal.sequence._pieces import piece_params
from tests.unit.worker.unreal.ue_scripts import test_audio_prep as audio
from tests.unit.worker.unreal.ue_scripts.test_range_prep import DUP, run

NO_LIBRARY = "Unreal's Movie Graph library cannot make a custom output size; the size cannot be set."
NOT_HELD = "The output size {w}x{h} did not hold on the farm's copy of the preset; a preset size would render."


def _named(world):
    return world['assets'][DUP].branches['Globals'][0].props['output_resolution']


def _swap_library(monkeypatch, library):
    """The ``unreal.MovieGraphLibrary`` the next ``make_unreal`` installs (``None``: none at all)."""
    monkeypatch.setattr(_ue_stub_seq, 'size_library', lambda: library)


class TestRenderedSize:

    def test_a_piece_renders_the_asked_size_not_the_presets_profile(self, tmp_path, monkeypatch):
        preset = ue.graph()
        result, error, world = run(tmp_path, monkeypatch, preset, piece_params(size=(640, 360), resolution=(640, 360)))
        assert error is None and result['ok'] is True, result
        named = _named(world)
        assert world['assets'][DUP].branches['Globals'][0].props['override_output_resolution'] is True
        assert (named.profile_name, ue.resolved_size(named)) == ('Custom', (640, 360))
        assert world['saved'][0] is world['assets'][DUP]
        original = preset.branches['Globals'][0].props['output_resolution']  # the artist's preset is never touched
        assert (original.profile_name, ue.resolved_size(original)) == ('1080p (FHD)', (1920, 1080))

    def test_a_standalone_frame_with_a_resolution(self, tmp_path, monkeypatch):
        p = params(frame=100, render_settings=dict(SETTINGS, **{'unreal.resolution': [1280, 720]}))
        result, error, world = run(tmp_path, monkeypatch, ue.graph(), p)
        assert error is None and result['ok'] is True, result
        assert ue.resolved_size(_named(world)) == (1280, 720)

    def test_without_a_resolution_the_preset_size_stays(self, tmp_path, monkeypatch):
        result, error, world = run(tmp_path, monkeypatch, ue.graph(), piece_params())
        assert error is None and result['ok'] is True, result
        props = world['assets'][DUP].branches['Globals'][0].props
        assert props['override_output_resolution'] is False and ue.resolved_size(_named(world)) == (1920, 1080)

    def test_the_audio_job_renders_128x72(self, tmp_path, monkeypatch):
        result, error, world = audio.run(tmp_path, monkeypatch, audio.audio_graph())
        assert error is None and result['ok'] is True, result
        named = _named(world)
        assert (named.profile_name, ue.resolved_size(named)) == ('Custom', (128, 72))


class TestNoSilentPresetSize:

    @pytest.mark.parametrize('library', [
        None,
        types.SimpleNamespace(is_named_resolution_valid=lambda name: False),
        types.SimpleNamespace(named_resolution_from_size=lambda w, h: None),
    ], ids=['no-library', 'no-from-size', 'no-is-valid'])
    def test_a_missing_library_fails_the_piece_prep(self, tmp_path, monkeypatch, library):
        _swap_library(monkeypatch, library)
        result, error, world = run(tmp_path, monkeypatch, ue.graph(), piece_params(size=(640, 360), resolution=(640, 360)))
        assert error is not None and result['error'] == NO_LIBRARY
        assert 'queue_asset' not in result and not world['saved'] and not world['queues'][0][1].jobs

    def test_only_the_cpp_name_is_no_library(self, tmp_path, monkeypatch):
        """UE never exposes ``MovieGraphBlueprintLibrary`` to Python; finding only it must not fall back."""
        monkeypatch.setattr(_ue_stub_seq, 'install', _with_cpp_name(_ue_stub_seq.install))
        _swap_library(monkeypatch, None)
        result, error, _world = run(tmp_path, monkeypatch, ue.graph(), piece_params(size=(640, 360), resolution=(640, 360)))
        assert error is not None and result['error'] == NO_LIBRARY

    def test_a_missing_library_fails_the_audio_prep(self, tmp_path, monkeypatch):
        _swap_library(monkeypatch, None)
        result, error, world = audio.run(tmp_path, monkeypatch, audio.audio_graph())
        assert error is not None and result['error'] == NO_LIBRARY and not world['saved']

    def test_a_value_naming_a_known_profile_fails(self, tmp_path, monkeypatch):
        """The old fallback's shape: the 1080p profile holding 640x360, which UE renders at 1920x1080."""
        old = ue.MovieGraphNamedResolution(resolution=ue.size(640, 360))
        assert ue.resolved_size(old) == (1920, 1080)
        library = _ue_stub_seq.size_library()
        library.named_resolution_from_size = lambda w, h: ue.MovieGraphNamedResolution(resolution=ue.size(w, h))
        _swap_library(monkeypatch, library)
        result, error, world = run(tmp_path, monkeypatch, ue.graph(), piece_params(size=(640, 360), resolution=(640, 360)))
        assert error is not None and result['error'] == NOT_HELD.format(w=640, h=360) and not world['saved']

    def test_a_value_holding_another_size_fails(self, tmp_path, monkeypatch):
        library = _ue_stub_seq.size_library()
        library.named_resolution_from_size = lambda w, h: ue.MovieGraphNamedResolution('Custom', ue.size(w + 1, h))
        _swap_library(monkeypatch, library)
        result, error, _world = audio.run(tmp_path, monkeypatch, audio.audio_graph())
        assert error is not None and result['error'] == NOT_HELD.format(w=128, h=72)


def _with_cpp_name(install):
    """``install``, plus a working library under the C++ name only."""
    working = _ue_stub_seq.size_library()

    def patched(module):
        install(module)
        module.MovieGraphBlueprintLibrary = working
        return module
    return patched


def test_no_ue_script_uses_a_cpp_library_name():
    """Python sees ``UMovieGraphBlueprintLibrary`` and ``UMoviePipelineBlueprintLibrary`` only by their ScriptNames
    (``MovieGraphLibrary``, ``MoviePipelineLibrary``); a C++ name is always missing at run time (#283)."""
    for script in sorted(job_dir.SCRIPTS_SOURCE.glob('*.py')):
        text = script.read_text(encoding='utf-8')
        for name in ('MovieGraphBlueprintLibrary', 'MoviePipelineBlueprintLibrary'):
            assert f'unreal.{name}' not in text and f'"{name}"' not in text, (script.name, name)
