# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The legacy pieces of the stub ``unreal`` module that sequence rendering's edits on
the farm's duplicate touch (``ue_scripts/sethlans_sequence_edits.py``): the EXR
output, the camera and console-variable settings, ``IntPoint`` and the graph
library's named resolution. ``_ue_stub`` re-exports them and ``install(module)``
adds the module-level helpers. The graph nodes live in ``_ue_stub``.
"""
import types

from tests.unit.worker.unreal._ue_stub_mrq import MoviePipelineOutputBase, _Setting, _Strict, size


class MoviePipelineImageSequenceOutput_EXR(MoviePipelineOutputBase):
    pass


class MoviePipelineCameraSetting(_Strict, _Setting):
    DEFAULTS = {'shutter_timing': 'FRAME_CENTER', 'overscan_percentage': 0.0}  # 5.8.4: no shutter angle


def cvar_entries(props):
    """Entries as UE 5.8's ``get_console_variables()`` returns them (``name``, ``value``, ``is_enabled``)."""
    if props.get('broken'):
        raise RuntimeError('unreadable')
    return [types.SimpleNamespace(name=n, value=v, is_enabled=on) for n, v, on in props.get('entries', [])]


class MoviePipelineConsoleVariableSetting(_Setting):
    def get_console_variables(self):
        return cvar_entries(self.props)


SEQ_LEGACY_CLASSES = (MoviePipelineImageSequenceOutput_EXR, MoviePipelineCameraSetting,
                      MoviePipelineConsoleVariableSetting)


def install(module):
    module.IntPoint = size
    module.MovieGraphBlueprintLibrary = types.SimpleNamespace(
        named_resolution_from_size=lambda width, height: types.SimpleNamespace(profile_name='Custom',
                                                                               resolution=size(width, height)))
    return module
