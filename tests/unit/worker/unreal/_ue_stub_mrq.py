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


class MovieGraphNode(_Props):
    """The graph node base; ``get_exposed_properties`` is UE 5.8.4's ``UMovieGraphNode`` call (none exposed)."""

    def get_exposed_properties(self):
        return []


class _Strict(_Props):
    """Properties are the class's ``DEFAULTS`` plus what is set; reading another one raises, as UE does."""
    DEFAULTS = {}

    def __init__(self, **props):
        super().__init__(**{**copy.deepcopy(self.DEFAULTS), **props})

    def get_editor_property(self, name):
        if name not in self.props:
            raise AttributeError(f"no property {name}")
        return self.props[name]


class Struct(types.SimpleNamespace):
    """A UE struct: its fields read as attributes and as editor properties (a missing one raises, as UE does)."""

    def get_editor_property(self, name):
        if not hasattr(self, name):
            raise AttributeError(f"no property {name}")
        return getattr(self, name)

    def set_editor_property(self, name, value):
        setattr(self, name, value)


def size(x, y):
    return Struct(x=x, y=y)


class _Setting(_Props):
    """A legacy setting; ``disabled`` is its ``is_enabled() == False`` (UE: ``bEnabled``)."""

    def is_enabled(self):
        return not self.disabled

    def set_is_enabled(self, value):
        self.disabled = not value


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

    def find_or_add_setting_by_class(self, cls, include_disabled_settings=False):
        """``UMoviePipelineConfigBase::FindOrAddSettingByClass``: a new one has the class defaults."""
        found = self.find_setting_by_class(cls, include_disabled_settings)
        if found is None:
            found = cls()
            self.settings.append(found)
        return found


LEGACY_CLASSES = (MoviePipelinePrimaryConfig, MoviePipelineGameOverrideSetting, MoviePipelineOutputSetting,
                  MoviePipelineAntiAliasingSetting, MoviePipelineOutputBase, MoviePipelineImageSequenceOutput_PNG,
                  MoviePipelineRenderPass)
