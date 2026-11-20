# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The base pieces of the stub ``unreal`` module (``_ue_stub``, which re-exports
all of this): frame rates, editor properties, and the legacy Movie Render
Queue preset with its settings.
"""
import copy
import types


class FrameRate:
    def __init__(self, numerator, denominator=1):
        self.numerator, self.denominator = numerator, denominator

    def __eq__(self, other):
        return isinstance(other, FrameRate) and (self.numerator, self.denominator) == (other.numerator, other.denominator)


class _Props:
    def __init__(self, **props):
        self.props = dict(props)
        self.disabled = props.pop('disabled', False)

    def set_editor_property(self, name, value):
        self.props[name] = value

    def get_editor_property(self, name):
        return self.props.get(name)

    def is_disabled(self):
        return self.disabled

    def set_disabled(self, value):
        self.disabled = value


class _Strict(_Props):
    """Properties are the class's ``DEFAULTS`` plus what is set; reading another one raises, as UE does."""
    DEFAULTS = {}

    def __init__(self, **props):
        super().__init__(**{**copy.deepcopy(self.DEFAULTS), **props})

    def get_editor_property(self, name):
        if name not in self.props:
            raise AttributeError(f"no property {name}")
        return self.props[name]


def size(x, y):
    return types.SimpleNamespace(x=x, y=y)


class _Setting(_Props):
    """A legacy setting; ``disabled`` is its ``is_enabled() == False`` (UE: ``bEnabled``)."""

    def is_enabled(self):
        return not self.disabled


class MoviePipelineGameOverrideSetting(_Setting):
    pass


class MoviePipelineOutputSetting(_Strict, _Setting):
    DEFAULTS = {'output_resolution': size(1920, 1080), 'use_custom_frame_rate': False,
                'output_frame_rate': FrameRate(24), 'zero_pad_frame_numbers': 4, 'frame_number_offset': 0,
                'handle_frame_count': 0, 'output_frame_step': 1}


class MoviePipelineAntiAliasingSetting(_Setting):
    pass


class MoviePipelineOutputBase(_Setting):
    pass


class MoviePipelineImageSequenceOutput_PNG(MoviePipelineOutputBase):
    pass


class MoviePipelineRenderPass(_Setting):
    pass


# UE 5.8.4 EMoviePipelineTextureStreamingMethod as Python exposes it (MoviePipelineGameOverrideSetting.h:13-22).
MoviePipelineTextureStreamingMethod = types.SimpleNamespace(NONE='NONE', DISABLED='DISABLED', FULLY_LOAD='FULLY_LOAD')


class MoviePipelinePrimaryConfig:
    """A legacy preset: its user settings, found like ``UMoviePipelineConfigBase::FindSettingByClass``."""

    def __init__(self, settings=()):
        self.settings = list(settings)

    def find_setting_by_class(self, cls, include_disabled_settings=False, exact_match=False):
        for setting in self.settings:
            if isinstance(setting, cls) and (include_disabled_settings or setting.is_enabled()):
                return setting
        return None

    def get_all_settings(self, include_disabled_settings=False, include_transient_settings=False):
        return [s for s in self.settings if include_disabled_settings or s.is_enabled()]


LEGACY_CLASSES = (MoviePipelinePrimaryConfig, MoviePipelineGameOverrideSetting, MoviePipelineOutputSetting,
                  MoviePipelineAntiAliasingSetting, MoviePipelineOutputBase, MoviePipelineImageSequenceOutput_PNG,
                  MoviePipelineRenderPass)
