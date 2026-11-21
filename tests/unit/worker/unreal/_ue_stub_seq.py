# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The legacy pieces of the stub ``unreal`` module that sequence rendering's edits on
the farm's duplicate touch (``ue_scripts/sethlans_sequence_edits.py``): the EXR
output, the camera and console-variable settings, ``IntPoint``, the named
resolution and the graph library that makes one (``resolved_size`` is what UE
renders). ``_ue_stub`` re-exports them and ``install(module)`` adds the
module-level helpers. The graph nodes live in ``_ue_stub``.
"""
import types

from tests.unit.worker.unreal._ue_stub_mrq import MovieGraphNode, MoviePipelineOutputBase, Struct, _Setting, _Strict, size


class MoviePipelineImageSequenceOutput_EXR(MoviePipelineOutputBase):
    pass


class MoviePipelineCameraSetting(_Strict, _Setting):
    DEFAULTS = {'shutter_timing': 'FRAME_CENTER', 'overscan_percentage': 0.0}  # 5.8.4: no shutter angle


def cvar_entries(entries):
    """``FMoviePipelineConsoleVariableEntry``s as UE 5.8's ``get_console_variables()`` returns them."""
    return [Struct(name=n, value=v, is_enabled=on) for n, v, on in entries]


class MoviePipelineConsoleVariableSetting(_Strict, _Setting):
    """``entries``: ``(name, value, enabled)``; the ``cvars`` property is ``ScriptNoExport`` in 5.8.4, never read."""
    DEFAULTS = {'console_variable_presets': []}

    def __init__(self, entries=(), **props):
        super().__init__(**props)
        self.entries = list(entries)

    def get_console_variables(self):
        return cvar_entries(self.entries)


SEQ_LEGACY_CLASSES = (MoviePipelineImageSequenceOutput_EXR, MoviePipelineCameraSetting,
                      MoviePipelineConsoleVariableSetting)


# UE 5.8.4's default Project Settings resolution profiles (MovieGraphProjectSettings.cpp:12-17).
PROFILES = {'720p (HD)': (1280, 720), '1080p (FHD)': (1920, 1080), '1440p (QHD)': (2560, 1440),
            '2160p (4K UHD)': (3840, 2160)}


class MovieGraphNamedResolution(Struct):
    """``FMovieGraphNamedResolution``: default-built it names the ``1080p (FHD)`` profile at 1920x1080
    (MovieGraphNamedResolution.h), so setting only ``resolution`` keeps that profile."""

    def __init__(self, profile_name='1080p (FHD)', resolution=None, description=''):
        super().__init__(profile_name=profile_name, resolution=resolution or size(1920, 1080), description=description)


class MovieGraphSetCVarValueNode(_Strict, MovieGraphNode):
    """UE 5.8.4's Set Console Variable node (``MovieGraphSetCVarValueNode.h``), the only graph node naming a CVar."""
    DEFAULTS = {'override_name': False, 'name': '', 'override_value': False, 'value': 0.0}


class MovieGraphApplyCVarPresetNode(_Strict, MovieGraphNode):
    DEFAULTS = {'override_console_variable_preset': False, 'console_variable_preset': None}


def resolved_size(named):
    """What UE 5.8.4 renders (``GetDesiredOutputResolution``): a profile Project Settings knows wins over the
    stored size; any other name renders the stored size."""
    if named.profile_name in PROFILES:
        return PROFILES[named.profile_name]
    return named.resolution.x, named.resolution.y


def size_library():
    """``unreal.MovieGraphLibrary``: ``UMovieGraphBlueprintLibrary`` by its Python ``ScriptName``; the C++ name
    is never exposed to Python."""
    return types.SimpleNamespace(
        named_resolution_from_size=lambda w, h: MovieGraphNamedResolution('Custom', size(w, h)),
        is_named_resolution_valid=lambda name: name in PROFILES,
        named_resolution_from_profile=lambda name: MovieGraphNamedResolution(name, size(*PROFILES[name])))


def install(module):
    module.IntPoint = size
    for cls in (MovieGraphNamedResolution, MovieGraphSetCVarValueNode, MovieGraphApplyCVarPresetNode):
        setattr(module, cls.__name__, cls)
    module.MovieGraphLibrary = size_library()
    return module
