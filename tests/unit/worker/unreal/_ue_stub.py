# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A stub of UE 5.8's ``unreal`` Python module, just enough to run the worker's
UE scripts (``ue_scripts/``) outside Unreal: Movie Render Graph presets,
level sequences with camera-cut and shot tracks, the asset library and the
queue. ``make_unreal(world)`` returns the module; ``world`` holds the assets
and records what the script did. The legacy preset and the base pieces live
in ``_ue_stub_mrq`` and are re-exported here.
"""
import copy
import types

from tests.unit.worker.unreal import _ue_stub_seq
from tests.unit.worker.unreal._ue_stub_mrq import (  # noqa: F401 - re-exported for the tests
    LEGACY_CLASSES, FrameRate, MoviePipelineAntiAliasingSetting, MoviePipelineGameOverrideSetting,
    MoviePipelineImageSequenceOutput_PNG, MoviePipelineOutputBase, MoviePipelineOutputSetting,
    MoviePipelinePrimaryConfig, MoviePipelineRenderPass, MoviePipelineTextureStreamingMethod, _Props, _Strict, size)
from tests.unit.worker.unreal._ue_stub_seq import (  # noqa: F401 - re-exported for the tests
    SEQ_LEGACY_CLASSES, MoviePipelineCameraSetting, MoviePipelineConsoleVariableSetting,
    MoviePipelineImageSequenceOutput_EXR)


class MovieGraphNode(_Props):
    pass


class MovieGraphGlobalOutputSettingNode(_Strict, MovieGraphNode):
    """UE 5.8.4's graph node, as far as discovery reads it: no frame-step property (reported absent, U-1)."""
    DEFAULTS = {'output_resolution': types.SimpleNamespace(profile_name='Custom', resolution=size(1920, 1080)),
                'override_output_resolution': False, 'output_frame_rate': FrameRate(24),
                'override_output_frame_rate': False, 'zero_pad_frame_numbers': 4,
                'override_zero_pad_frame_numbers': False, 'frame_number_offset': 0,
                'override_frame_number_offset': False, 'handle_frame_count': 0, 'override_handle_frame_count': False}


class MovieGraphSamplingMethodNode(MovieGraphNode):
    pass


class MovieGraphAudioOutputNode(MovieGraphNode):
    pass


class MovieGraphGlobalGameOverridesNode(MovieGraphNode):
    pass


class MovieGraphFileOutputNode(MovieGraphNode):
    pass


class MovieGraphSubgraphNode(MovieGraphNode):
    pass


class MovieGraphImageSequenceOutputNode_PNG(MovieGraphFileOutputNode):
    pass


class MovieGraphImageSequenceOutputNode_EXR(MovieGraphFileOutputNode):
    pass


class MovieGraphRenderLayerNode(MovieGraphNode):
    pass


class MovieGraphImagePassBaseNode(MovieGraphNode):
    pass


class MovieGraphRenderPassNode(MovieGraphImagePassBaseNode):
    pass


class MovieGraphDeferredRenderPassNode(MovieGraphRenderPassNode):
    pass


class MovieGraphCameraSettingNode(_Strict, MovieGraphNode):
    DEFAULTS = {'override_shutter_timing': False, 'shutter_timing': 'FRAME_CENTER'}  # 5.8.4: no shutter angle


class MovieGraphConsoleVariableNode(MovieGraphNode):
    def get_console_variables(self):
        return _ue_stub_seq.cvar_entries(self.props)


class MovieGraphConfig:
    def __init__(self, branches, subgraphs=()):
        self.branches, self.subgraphs = branches, list(subgraphs)

    def get_branch_names(self):
        return list(self.branches)

    def get_nodes_for_branch(self, cls, branch, recursive):
        return [node for node in self.branches[branch] if isinstance(node, cls)]

    def get_all_contained_subgraphs(self):
        return self.subgraphs

    def get_class(self):
        return types.SimpleNamespace(get_name=lambda: 'MovieGraphConfig')


class TimeWarp:
    """UE 5.8.4's ``MovieSceneTimeWarpVariant``: ``to_fixed_play_rate()`` gives the rate; a curve gives 1.0 (the
    engine only logs an error); ``broken`` makes it unreadable."""

    def __init__(self, play_rate=1.0, curve=False, broken=False):
        self.play_rate, self.curve, self.broken = play_rate, curve, broken

    def to_fixed_play_rate(self):
        if self.broken:
            raise RuntimeError('unreadable')
        return 1.0 if self.curve else self.play_rate


class _Struct(types.SimpleNamespace):
    def get_editor_property(self, name):
        return getattr(self, name)


class Section:
    def __init__(self, start, end, sequence=None, offset_ticks=0, active=True, row=0, time_scale=None):
        self.start, self.end, self.sequence, self.active, self.row = start, end, sequence, active, row
        self.parameters = _Struct(start_frame_offset=types.SimpleNamespace(value=offset_ticks),
                                  time_scale=time_scale or TimeWarp())

    def get_editor_property(self, name):
        return getattr(self, name)

    def get_row_index(self):
        return self.row

    def get_start_frame(self):
        return self.start

    def get_end_frame(self):
        return self.end

    def is_active(self):
        return self.active

    def get_sequence(self):
        return self.sequence


class Track:
    """No mute or eval-disabled accessor: UE 5.8.4 does not expose them to Python (probe)."""

    def __init__(self, sections):
        self.sections = sections

    def get_sections(self):
        return self.sections


class MovieSceneCameraCutTrack(Track):
    pass


class MovieSceneCinematicShotTrack(Track):
    pass


class MovieSceneSubTrack(Track):
    pass


class Sequence:
    def __init__(self, start, end, tracks=(), rate=24, ticks=24000, name='Seq'):
        self.start, self.end, self.tracks, self.rate, self.ticks, self.name = start, end, list(tracks), rate, ticks, name

    def get_display_rate(self):
        return FrameRate(*self.rate) if isinstance(self.rate, tuple) else FrameRate(self.rate)

    def get_tick_resolution(self):
        return FrameRate(self.ticks)

    def get_playback_start(self):
        return self.start

    def get_playback_end(self):
        return self.end

    def get_tracks(self):
        return self.tracks

    def get_name(self):
        return self.name


class Queue:
    def __init__(self):
        self.jobs = []

    def allocate_new_job(self, cls):
        job = types.SimpleNamespace(graph=None, origin=None, author='')
        job.set_graph_preset = lambda preset: setattr(job, 'graph', preset)
        job.set_preset_origin = lambda preset: setattr(job, 'origin', preset)
        self.jobs.append(job)
        return job


def make_unreal(world):
    """``world``: {'assets': {path: object}, 'cvars': {name: value}, 'existing_root': bool}."""
    world.setdefault('saved', [])
    world.setdefault('logs', [])
    world.setdefault('queues', [])
    module = types.ModuleType('unreal')
    for cls in (FrameRate, MovieGraphNode, MovieGraphGlobalOutputSettingNode, MovieGraphGlobalGameOverridesNode,
                MovieGraphFileOutputNode, MovieGraphImageSequenceOutputNode_PNG, MovieGraphRenderLayerNode,
                MovieGraphRenderPassNode, MovieGraphConfig, MovieSceneCameraCutTrack, MovieSceneCinematicShotTrack,
                MovieSceneSubTrack, MovieGraphSubgraphNode, MovieGraphImageSequenceOutputNode_EXR,
                MovieGraphAudioOutputNode, MovieGraphSamplingMethodNode, MovieGraphImagePassBaseNode,
                MovieGraphDeferredRenderPassNode, MovieGraphCameraSettingNode,
                MovieGraphConsoleVariableNode) + LEGACY_CLASSES + SEQ_LEGACY_CLASSES:
        setattr(module, cls.__name__, cls)
    _ue_stub_seq.install(module)
    module.get_default_object = lambda cls: cls()  # a fresh instance holds the class defaults
    module.MoviePipelineTextureStreamingMethod = MoviePipelineTextureStreamingMethod
    module.log = world['logs'].append
    module.MovieGraphSequenceRangeType = types.SimpleNamespace(CUSTOM='custom')
    module.MovieGraphSequencePlaybackRangeBound = lambda: types.SimpleNamespace(type=None, value=None)
    module.DirectoryPath = lambda path: types.SimpleNamespace(path=path)
    module.SoftObjectPath = lambda path: path
    module.MoviePipelineQueue = Queue
    module.MoviePipelineExecutorJob = object
    module.load_asset = lambda path: world['assets'].get(path)
    registry = types.SimpleNamespace(search_all_assets=lambda sync: world.__setitem__('searched', sync),
                                     wait_for_completion=lambda: None)
    module.AssetRegistryHelpers = types.SimpleNamespace(get_asset_registry=lambda: registry)

    def duplicate(src, dst):
        dup = copy.deepcopy(world['assets'][src])
        world['assets'][dst] = dup
        return dup

    def create_asset(name, root, cls, factory):
        queue = cls()
        world['queues'].append((f"{root}/{name}", queue))
        return queue
    module.EditorAssetLibrary = types.SimpleNamespace(
        does_directory_exist=lambda root: world.get('existing_root', False),
        list_assets=lambda root, recursive, folders: ['x'] if world.get('existing_root') else [],
        duplicate_asset=duplicate, save_loaded_asset=lambda asset, only_dirty: world['saved'].append(asset))
    module.AssetToolsHelpers = types.SimpleNamespace(
        get_asset_tools=lambda: types.SimpleNamespace(create_asset=create_asset))
    module.SystemLibrary = types.SimpleNamespace(
        get_console_variable_string_value=lambda name: world.get('cvars', {}).get(name, ''))
    return module


def overrides_node(scalability=None, streaming=None, disabled=False):
    """A Global Game Overrides node; ``None`` leaves that value un-overridden (UE's ``bOverride_*`` false)."""
    return MovieGraphGlobalGameOverridesNode(
        override_scalability_quality_level=scalability is not None,
        scalability_quality_level=types.SimpleNamespace(value=4 if scalability is None else scalability),
        override_disable_texture_streaming=streaming is not None, disable_texture_streaming=bool(streaming),
        disabled=disabled)


def graph(layers=1, passes=1, png=True, extra_outputs=(), subgraphs=(), scalability=None, overrides=()):
    nodes = {'Globals': [MovieGraphGlobalOutputSettingNode()], 'DefaultLayer': []}
    if scalability is not None:
        nodes['Globals'].append(overrides_node(scalability=scalability))
    nodes['Globals'].extend(overrides)
    for index in range(layers):
        nodes['DefaultLayer'].append(MovieGraphRenderLayerNode(layer_name=f'l{index}'))
    for _ in range(passes):
        nodes['DefaultLayer'].append(MovieGraphRenderPassNode())
    if png:
        nodes['DefaultLayer'].append(MovieGraphImageSequenceOutputNode_PNG())
    nodes['DefaultLayer'].extend(extra_outputs)
    return MovieGraphConfig(nodes, subgraphs)


def shot(start, end, inner_cuts, inner_start=0, offset_frames=0, name='Seq', active=True, inner_tracks=(), **section):
    """A shot section whose sub-sequence has its own camera cuts (24 fps, 24,000 ticks); ``section``: row, scale."""
    tracks = [MovieSceneCameraCutTrack([Section(s, e) for s, e in inner_cuts])] + list(inner_tracks)
    sub = Sequence(inner_start, inner_start + 10000, tracks, name=name)
    return Section(start, end, sequence=sub, offset_ticks=offset_frames * 1000, active=active, **section)
